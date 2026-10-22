package controller

import (
	"context"
	"errors"
	"fmt"
	"sync"
	"testing"
	"time"

	"connectrpc.com/connect"
	mupv1 "github.com/Ytaihei/free5gc-srv6-mup-lab/api/mup/v1"
	"github.com/Ytaihei/free5gc-srv6-mup-lab/internal/bgp"
	"github.com/Ytaihei/free5gc-srv6-mup-lab/internal/config"
	"github.com/Ytaihei/free5gc-srv6-mup-lab/internal/model"
	"google.golang.org/protobuf/types/known/emptypb"
)

func TestSnapshotPolicySuppressionAndLeaseWithdrawal(t *testing.T) {
	cfg := config.Config{
		Observer:   config.Observer{ID: "observer"},
		Controller: config.Controller{LocalASN: 65000, RouterID: "192.0.2.14", ListenPort: -1, NextHop: "192.0.2.14", Lease: 15 * time.Second},
		MUP:        config.MUP{SessionRD: "65000:10", T1RouteTarget: "65000:100", T2RouteTarget: "65000:200", DirectSegmentASN: 65000, DirectSegmentID: 1, TEIDPrefixLength: 32},
		Policy:     []config.PolicyRule{{Name: "selected", DNN: "internet", UEPrefix: "10.60.0.0/16", Action: "direct"}},
	}
	speaker := bgp.New(cfg)
	ctx := context.Background()
	if err := speaker.Start(ctx); err != nil {
		t.Fatal(err)
	}
	defer speaker.Stop(ctx)
	c := New(cfg, speaker)
	s := model.Session{
		Key: "a:b", CPSEID: 1, UPSEID: 2, UEIPv4: "10.60.0.1", DNN: "internet",
		UPFGTPIPv4: "10.210.32.10", UplinkTEID: 100,
		RANGTPIPv4: "10.210.31.11", DownlinkTEID: 200, QFI: 9, ObservedAt: time.Now(),
	}
	rsp, err := c.SyncSnapshot(ctx, connect.NewRequest(&mupv1.SyncSnapshotRequest{
		ObserverId: "observer", Generation: 1, ObservedUnixNano: time.Now().UnixNano(), Sessions: []*mupv1.Session{s.Proto()},
	}))
	if err != nil {
		t.Fatal(err)
	}
	if rsp.Msg.SelectedSessions != 1 {
		t.Fatalf("selected = %d", rsp.Msg.SelectedSessions)
	}
	listed, _ := c.ListSessions(ctx, connect.NewRequest(&mupv1.ListSessionsRequest{}))
	if len(listed.Msg.Sessions) != 1 || !listed.Msg.Sessions[0].Advertised {
		t.Fatalf("sessions = %+v", listed.Msg.Sessions)
	}

	// A malformed snapshot must fail atomically, without refreshing the
	// observer lease, consuming its generation, or replacing live routes.
	lastSeen := c.lastSeen
	for _, qfi := range []uint32{64, 255, 256, 257, 319, 512, 65536, ^uint32(0)} {
		t.Run(fmt.Sprintf("invalid-wire-qfi-%d", qfi), func(t *testing.T) {
			invalid := s.Proto()
			invalid.Key = "invalid"
			invalid.Qfi = qfi
			_, err := c.SyncSnapshot(ctx, connect.NewRequest(&mupv1.SyncSnapshotRequest{
				ObserverId: "observer", Generation: 2, Sessions: []*mupv1.Session{invalid},
			}))
			if connect.CodeOf(err) != connect.CodeInvalidArgument {
				t.Fatalf("invalid QFI %d: want InvalidArgument, got %v", qfi, err)
			}
			if c.generation != 1 || !c.lastSeen.Equal(lastSeen) || len(c.sessions) != 1 {
				t.Fatalf("rejected snapshot changed generation, lease, or sessions")
			}
			current := c.sessions[s.Key]
			if current == nil || !current.advertised || current.session.QFI != s.QFI {
				t.Fatalf("rejected snapshot replaced the live session: %+v", current)
			}
		})
	}
	// The rejected generation is still usable by the legitimate observer.
	_, err = c.SyncSnapshot(ctx, connect.NewRequest(&mupv1.SyncSnapshotRequest{
		ObserverId: "observer", Generation: 2, Sessions: []*mupv1.Session{s.Proto()},
	}))
	if err != nil {
		t.Fatalf("valid snapshot after rejection: %v", err)
	}

	muted, err := c.SetSuppression(ctx, connect.NewRequest(&mupv1.SetSuppressionRequest{Key: s.Key, Suppressed: true}))
	if err != nil || muted.Msg.Session.Advertised || muted.Msg.Session.Reason != "operator:suppressed" {
		t.Fatalf("suppress = %+v, %v", muted, err)
	}
	_, err = c.SetSuppression(ctx, connect.NewRequest(&mupv1.SetSuppressionRequest{Key: s.Key, Suppressed: false}))
	if err != nil {
		t.Fatal(err)
	}

	c.mu.Lock()
	if err := c.reconcileLocked(false); err != nil {
		t.Fatal(err)
	}
	if c.sessions[s.Key].advertised || c.sessions[s.Key].reason != "observer:lease-expired" {
		t.Fatalf("expired session = %+v", c.sessions[s.Key])
	}
	c.mu.Unlock()
}

// This fake tests controller scheduling and error propagation; the actual RIB
// cleanup, retry and peer delivery are tested with real GoBGP in package bgp.
type cleanupProbe struct {
	mu      sync.Mutex
	pending bool
	fail    bool
	retried chan struct{}
}

func (p *cleanupProbe) Advertise(model.Session) (bgp.Advertisement, error) {
	return bgp.Advertisement{}, errors.New("unexpected advertisement")
}
func (p *cleanupProbe) Withdraw(bgp.Advertisement) error {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.pending = true
	return errors.New("injected removal failure")
}
func (p *cleanupProbe) RetryWithdrawals() error {
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.fail {
		return errors.New("injected retry failure")
	}
	p.pending = false
	select {
	case p.retried <- struct{}{}:
	default:
	}
	return nil
}
func (p *cleanupProbe) PeerState(context.Context) string { return "2/2 established" }

func TestRemovedSessionRetainsSpeakerCleanupAndHealthyLeaseRetries(t *testing.T) {
	cfg := config.Config{Observer: config.Observer{ID: "observer"}, Controller: config.Controller{Lease: time.Hour}}
	c := New(cfg, nil)
	p := &cleanupProbe{fail: true, retried: make(chan struct{}, 1)}
	c.bgp = p
	c.sessions["removed"] = &controlled{advertised: true}
	_, err := c.SyncSnapshot(context.Background(), connect.NewRequest(&mupv1.SyncSnapshotRequest{
		ObserverId: "observer", Generation: 1,
	}))
	if connect.CodeOf(err) != connect.CodeInternal {
		t.Fatalf("withdrawal failure not reported: %v", err)
	}
	if len(c.sessions) != 0 || !p.pending {
		t.Fatal("removed session or pending cleanup state incorrect")
	}
	p.mu.Lock()
	p.fail = false
	p.mu.Unlock()
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() { defer close(done); c.RunLease(ctx) }()
	defer func() { cancel(); <-done }()
	select {
	case <-p.retried:
	case <-time.After(3 * time.Second):
		t.Fatal("healthy lease did not retry cleanup for removed session")
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.pending {
		t.Fatal("cleanup still pending")
	}
}

func TestReconcileBlocksNewWorkUntilPendingCleanupSucceeds(t *testing.T) {
	c := New(config.Config{}, nil)
	p := &cleanupProbe{fail: true, pending: true, retried: make(chan struct{}, 1)}
	c.bgp = p
	if err := c.reconcileLocked(false); err == nil {
		t.Fatal("pending withdrawal error ignored")
	}
	if !p.pending {
		t.Fatal("failed retry lost cleanup state")
	}
	p.fail = false
	if err := c.reconcileLocked(false); err != nil {
		t.Fatal(err)
	}
	if p.pending {
		t.Fatal("successful retry kept pending state")
	}
}

// Distinguish pending cleanup from an independently removable advertisement.
// Unlike cleanupProbe, this fake permits successful withdrawals while retry fails.
type withdrawalProbe struct {
	retryErr    error
	withdrawErr error
	withdrawals int
	advertised  []string
}

func (p *withdrawalProbe) Advertise(s model.Session) (bgp.Advertisement, error) {
	p.advertised = append(p.advertised, s.Key)
	return bgp.Advertisement{}, nil
}
func (p *withdrawalProbe) Withdraw(bgp.Advertisement) error {
	p.withdrawals++
	if p.withdrawErr != nil {
		p.retryErr = p.withdrawErr
	}
	return p.withdrawErr
}
func (p *withdrawalProbe) RetryWithdrawals() error          { return p.retryErr }
func (p *withdrawalProbe) PeerState(context.Context) string { return "2/2 established" }

func withdrawalController() (*Controller, *withdrawalProbe) {
	cfg := config.Config{Controller: config.Controller{Lease: time.Hour},
		Policy: []config.PolicyRule{{Name: "internet", DNN: "internet", UEPrefix: "10.60.0.0/16", Action: "direct"}}}
	c := New(cfg, nil)
	p := &withdrawalProbe{}
	c.bgp = p
	c.lastSeen = time.Now()
	s := model.Session{Key: "existing", UEIPv4: "10.60.0.1", DNN: "internet",
		UPFGTPIPv4: "10.210.32.10", UplinkTEID: 100, RANGTPIPv4: "10.210.31.11", DownlinkTEID: 200, QFI: 9}
	c.sessions[s.Key] = &controlled{session: s, advertised: true, fingerprint: sessionFingerprint(s)}
	s.Key, s.UEIPv4, s.UplinkTEID = "new", "10.60.0.2", 101
	c.sessions[s.Key] = &controlled{session: s}
	return c, p
}

func TestPendingCleanupDoesNotBlockRequiredWithdrawals(t *testing.T) {
	for _, scenario := range []string{"expired-lease", "suppressed", "policy-unselected", "changed-fteid"} {
		for _, pendingFailure := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/pending=%v", scenario, pendingFailure), func(t *testing.T) {
				c, p := withdrawalController()
				pendingErr := errors.New("unrelated pending cleanup failed")
				if pendingFailure {
					p.retryErr = pendingErr
				}
				wantAds := 1 // The new session may be advertised only after successful cleanup.
				switch scenario {
				case "expired-lease":
					c.lastSeen = time.Now().Add(-2 * time.Hour)
					wantAds = 0
				case "policy-unselected":
					c.cfg.Policy = nil
					wantAds = 0
				case "changed-fteid":
					c.sessions["existing"].session.UplinkTEID += 100
					wantAds = 2 // Existing session must be replaced too.
				}
				var err error
				if scenario == "suppressed" {
					_, err = c.SetSuppression(context.Background(), connect.NewRequest(&mupv1.SetSuppressionRequest{Key: "existing", Suppressed: true}))
				} else {
					_, err = c.Reconcile(context.Background(), connect.NewRequest(&emptypb.Empty{}))
				}
				if pendingFailure {
					if connect.CodeOf(err) != connect.CodeInternal || !errors.Is(err, pendingErr) {
						t.Fatalf("pending cleanup error not preserved: %v", err)
					}
				} else if err != nil {
					t.Fatal(err)
				}
				if p.withdrawals != 1 {
					t.Fatalf("required withdrawal attempts = %d, want 1", p.withdrawals)
				}
				if pendingFailure {
					if len(p.advertised) != 0 || c.sessions["existing"].advertised {
						t.Fatalf("advertised while cleanup failed: %v", p.advertised)
					}
					p.retryErr = nil
					if _, err := c.Reconcile(context.Background(), connect.NewRequest(&emptypb.Empty{})); err != nil {
						t.Fatal(err)
					}
				}
				if len(p.advertised) != wantAds || p.withdrawals != 1 {
					t.Fatalf("after cleanup: advertisements=%v withdrawals=%d, want %d and 1", p.advertised, p.withdrawals, wantAds)
				}
			})
		}
	}
}

func TestNewWithdrawalFailureStillAttemptsEveryWithdrawalBeforeAdvertising(t *testing.T) {
	for attempt := 0; attempt < 20; attempt++ {
		// Map iteration must not let a new session advertise before a later
		// required withdrawal fails, nor stop the remaining withdrawals.
		c, p := withdrawalController()
		first := c.sessions["existing"]
		first.suppressed = true
		second := *first
		second.session.Key = "second"
		second.session.UEIPv4, second.session.UplinkTEID, second.session.DownlinkTEID = "10.60.0.3", 102, 202
		second.fingerprint = sessionFingerprint(second.session)
		c.sessions["second"] = &second
		withdrawErr := errors.New("new withdrawal failed")
		p.withdrawErr = withdrawErr
		err := c.reconcileLocked(true)
		if !errors.Is(err, withdrawErr) || p.withdrawals != 2 || len(p.advertised) != 0 {
			t.Fatalf("attempt %d: err=%v withdrawals=%d advertisements=%v", attempt, err, p.withdrawals, p.advertised)
		}
		if first.advertised || second.advertised {
			t.Fatal("controller retained stale advertisement handles")
		}
		p.withdrawErr, p.retryErr = nil, nil
		if err := c.reconcileLocked(true); err != nil {
			t.Fatal(err)
		}
		if p.withdrawals != 2 || len(p.advertised) != 1 || p.advertised[0] != "new" {
			t.Fatalf("cleanup recovery: withdrawals=%d advertisements=%v", p.withdrawals, p.advertised)
		}
	}
}
