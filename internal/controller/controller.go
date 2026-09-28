package controller

import (
	"context"
	"fmt"
	"sort"
	"sync"
	"time"

	"connectrpc.com/connect"
	"google.golang.org/protobuf/types/known/emptypb"

	mupv1 "github.com/Ytaihei/free5gc-srv6-mup-lab/api/mup/v1"
	"github.com/Ytaihei/free5gc-srv6-mup-lab/internal/bgp"
	"github.com/Ytaihei/free5gc-srv6-mup-lab/internal/config"
	"github.com/Ytaihei/free5gc-srv6-mup-lab/internal/model"
	"github.com/Ytaihei/free5gc-srv6-mup-lab/internal/policy"
)

type controlled struct {
	session     model.Session
	selected    bool
	suppressed  bool
	reason      string
	advertised  bool
	fingerprint string
	adv         bgp.Advertisement
}

type Controller struct {
	mu         sync.Mutex
	cfg        config.Config
	bgp        *bgp.Speaker
	observerID string
	generation uint64
	lastSeen   time.Time
	sessions   map[string]*controlled
	suppressed map[string]bool
}

func New(cfg config.Config, speaker *bgp.Speaker) *Controller {
	return &Controller{cfg: cfg, bgp: speaker, sessions: make(map[string]*controlled), suppressed: make(map[string]bool)}
}

func (c *Controller) SyncSnapshot(_ context.Context, req *connect.Request[mupv1.SyncSnapshotRequest]) (*connect.Response[mupv1.SyncSnapshotResponse], error) {
	now := time.Now()
	if req.Msg.ObserverId == "" || req.Msg.Generation == 0 {
		return nil, connect.NewError(connect.CodeInvalidArgument, fmt.Errorf("observer_id and generation are required"))
	}
	if req.Msg.ObserverId != c.cfg.Observer.ID {
		return nil, connect.NewError(connect.CodePermissionDenied, fmt.Errorf("unexpected observer_id %q", req.Msg.ObserverId))
	}
	next := make(map[string]model.Session, len(req.Msg.Sessions))
	for _, p := range req.Msg.Sessions {
		s, err := model.FromProto(p)
		if err != nil {
			return nil, connect.NewError(connect.CodeInvalidArgument, err)
		}
		if _, duplicate := next[s.Key]; duplicate {
			return nil, connect.NewError(connect.CodeInvalidArgument, fmt.Errorf("duplicate session key %s", s.Key))
		}
		next[s.Key] = s
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if req.Msg.ObserverId == c.observerID && req.Msg.Generation <= c.generation {
		return nil, connect.NewError(connect.CodeAlreadyExists, fmt.Errorf("stale snapshot generation %d", req.Msg.Generation))
	}
	c.observerID, c.generation, c.lastSeen = req.Msg.ObserverId, req.Msg.Generation, now
	for key, current := range c.sessions {
		if _, ok := next[key]; !ok {
			if current.advertised {
				_ = c.bgp.Withdraw(current.adv)
			}
			delete(c.sessions, key)
		}
	}
	for key, session := range next {
		current, ok := c.sessions[key]
		if !ok {
			current = &controlled{}
			c.sessions[key] = current
		}
		current.session = session
		current.suppressed = c.suppressed[key]
	}
	if err := c.reconcileLocked(true); err != nil {
		return nil, connect.NewError(connect.CodeInternal, err)
	}
	return connect.NewResponse(&mupv1.SyncSnapshotResponse{
		AcceptedGeneration: c.generation, SelectedSessions: uint32(c.selectedCountLocked()),
	}), nil
}

func (c *Controller) Status(ctx context.Context, _ *connect.Request[mupv1.StatusRequest]) (*connect.Response[mupv1.StatusResponse], error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	valid := c.leaseValidLocked(time.Now())
	adv := 0
	for _, s := range c.sessions {
		if s.advertised {
			adv += 2
		}
	}
	return connect.NewResponse(&mupv1.StatusResponse{
		ObserverId: c.observerID, Generation: c.generation,
		ObserverLastSeenUnixNano: c.lastSeen.UnixNano(), ObserverLeaseValid: valid,
		ObservedSessions: uint32(len(c.sessions)), SelectedSessions: uint32(c.selectedCountLocked()),
		AdvertisedRoutes: uint32(adv), BgpState: c.bgp.PeerState(ctx),
	}), nil
}

func (c *Controller) ListSessions(_ context.Context, _ *connect.Request[mupv1.ListSessionsRequest]) (*connect.Response[mupv1.ListSessionsResponse], error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	keys := make([]string, 0, len(c.sessions))
	for key := range c.sessions {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	out := make([]*mupv1.ControlledSession, 0, len(keys))
	for _, key := range keys {
		out = append(out, toProto(c.sessions[key]))
	}
	return connect.NewResponse(&mupv1.ListSessionsResponse{Sessions: out}), nil
}

func (c *Controller) SetSuppression(_ context.Context, req *connect.Request[mupv1.SetSuppressionRequest]) (*connect.Response[mupv1.SetSuppressionResponse], error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	s, ok := c.sessions[req.Msg.Key]
	if !ok {
		return nil, connect.NewError(connect.CodeNotFound, fmt.Errorf("unknown session %s", req.Msg.Key))
	}
	if req.Msg.Suppressed {
		c.suppressed[req.Msg.Key] = true
	} else {
		delete(c.suppressed, req.Msg.Key)
	}
	s.suppressed = req.Msg.Suppressed
	if err := c.reconcileLocked(c.leaseValidLocked(time.Now())); err != nil {
		return nil, connect.NewError(connect.CodeInternal, err)
	}
	return connect.NewResponse(&mupv1.SetSuppressionResponse{Session: toProto(s)}), nil
}

func (c *Controller) Reconcile(_ context.Context, _ *connect.Request[emptypb.Empty]) (*connect.Response[mupv1.ReconcileResponse], error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if err := c.reconcileLocked(c.leaseValidLocked(time.Now())); err != nil {
		return nil, connect.NewError(connect.CodeInternal, err)
	}
	adv := 0
	for _, s := range c.sessions {
		if s.advertised {
			adv += 2
		}
	}
	return connect.NewResponse(&mupv1.ReconcileResponse{SelectedSessions: uint32(c.selectedCountLocked()), AdvertisedRoutes: uint32(adv)}), nil
}

func (c *Controller) RunLease(ctx context.Context) {
	ticker := time.NewTicker(time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case now := <-ticker.C:
			c.mu.Lock()
			if !c.leaseValidLocked(now) {
				_ = c.reconcileLocked(false)
			}
			c.mu.Unlock()
		}
	}
}

func (c *Controller) leaseValidLocked(now time.Time) bool {
	return !c.lastSeen.IsZero() && now.Sub(c.lastSeen) <= c.cfg.Controller.Lease
}

func (c *Controller) reconcileLocked(leaseValid bool) error {
	var firstErr error
	for _, s := range c.sessions {
		decision := policy.Evaluate(c.cfg.Policy, s.session)
		s.selected = decision.Selected && !s.suppressed && leaseValid
		s.reason = decision.Reason
		if s.suppressed {
			s.reason = "operator:suppressed"
		}
		if !leaseValid {
			s.reason = "observer:lease-expired"
		}
		fp := sessionFingerprint(s.session)
		if s.advertised && (!s.selected || fp != s.fingerprint) {
			if err := c.bgp.Withdraw(s.adv); err != nil && firstErr == nil {
				firstErr = err
			}
			s.advertised, s.adv = false, bgp.Advertisement{}
		}
		if s.selected && !s.advertised {
			adv, err := c.bgp.Advertise(s.session)
			if err != nil {
				s.reason = "bgp:error:" + err.Error()
				if firstErr == nil {
					firstErr = err
				}
				continue
			}
			s.adv, s.advertised, s.fingerprint = adv, true, fp
		}
	}
	return firstErr
}

func (c *Controller) selectedCountLocked() int {
	n := 0
	for _, s := range c.sessions {
		if s.selected {
			n++
		}
	}
	return n
}

func sessionFingerprint(s model.Session) string {
	return fmt.Sprintf("%s|%s|%d|%s|%d|%d", s.UEIPv4, s.RANGTPIPv4, s.DownlinkTEID, s.UPFGTPIPv4, s.UplinkTEID, s.QFI)
}

func toProto(s *controlled) *mupv1.ControlledSession {
	return &mupv1.ControlledSession{Session: s.session.Proto(), Selected: s.selected, Suppressed: s.suppressed, Reason: s.reason, Advertised: s.advertised}
}
