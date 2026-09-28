package controller

import (
	"context"
	"fmt"
	"testing"
	"time"

	"connectrpc.com/connect"
	mupv1 "github.com/Ytaihei/free5gc-srv6-mup-lab/api/mup/v1"
	"github.com/Ytaihei/free5gc-srv6-mup-lab/internal/bgp"
	"github.com/Ytaihei/free5gc-srv6-mup-lab/internal/config"
	"github.com/Ytaihei/free5gc-srv6-mup-lab/internal/model"
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
