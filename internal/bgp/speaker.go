package bgp

import (
	"context"
	"encoding/binary"
	"errors"
	"fmt"
	"net/netip"
	"sync"

	"github.com/Ytaihei/free5gc-srv6-mup-lab/internal/config"
	"github.com/Ytaihei/free5gc-srv6-mup-lab/internal/model"
	"github.com/google/uuid"
	api "github.com/osrg/gobgp/v4/api"
	"github.com/osrg/gobgp/v4/pkg/apiutil"
	pkt "github.com/osrg/gobgp/v4/pkg/packet/bgp"
	"github.com/osrg/gobgp/v4/pkg/server"
)

type Advertisement struct {
	T1   uuid.UUID
	T2   uuid.UUID
	pair *routePair
}

// Each pair owns its local NLRIs until withdrawal succeeds. Keeping ownership
// separate from UUIDs also covers GoBGP errors after installation but before
// UUID creation. The pointer is an opaque, process-local generation handle.
type routePair struct {
	paths   []*apiutil.Path
	pending bool
}

type pathAPI interface {
	AddPath(apiutil.AddPathRequest) ([]apiutil.AddPathResponse, error)
	DeletePath(apiutil.DeletePathRequest) error
}

type Speaker struct {
	mu     sync.Mutex
	srv    *server.BgpServer
	cfg    config.Config
	paths  pathAPI
	pairs  map[*routePair]struct{}
	owners map[string]*routePair
}

func New(cfg config.Config) *Speaker { return &Speaker{cfg: cfg} }

func (s *Speaker) Start(ctx context.Context) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.srv != nil {
		return fmt.Errorf("BGP speaker already started")
	}
	srv := server.NewBgpServer()
	go srv.Serve()
	if err := srv.StartBgp(ctx, &api.StartBgpRequest{Global: &api.Global{
		Asn: s.cfg.Controller.LocalASN, RouterId: s.cfg.Controller.RouterID,
		ListenPort: s.cfg.Controller.ListenPort,
	}}); err != nil {
		srv.Stop()
		return err
	}
	for _, peer := range s.cfg.Controller.Peers {
		err := srv.AddPeer(ctx, &api.AddPeerRequest{Peer: &api.Peer{
			Conf:   &api.PeerConf{NeighborAddress: peer.Address, PeerAsn: peer.ASN},
			Timers: &api.Timers{Config: &api.TimersConfig{HoldTime: 15, KeepaliveInterval: 5, ConnectRetry: 3}},
			// MUP-C is the route reflector for the packet edges.  Besides the
			// locally originated T1/T2 routes, it must reflect MUP PE (N3/Interwork side) ISD and MUP PE (N6/Direct side)
			// DSD discovery routes between its two iBGP clients.
			RouteReflector: &api.RouteReflector{
				RouteReflectorClient: true, RouteReflectorClusterId: s.cfg.Controller.RouterID,
			},
			AfiSafis: []*api.AfiSafi{{Config: &api.AfiSafiConfig{
				Family: &api.Family{Afi: api.Family_AFI_IP, Safi: api.Family_SAFI_MUP}, Enabled: true,
			}}},
		}})
		if err != nil {
			srv.Stop()
			return fmt.Errorf("add BGP peer %s: %w", peer.Address, err)
		}
	}
	s.srv = srv
	s.paths = srv
	s.pairs = make(map[*routePair]struct{})
	s.owners = make(map[string]*routePair)
	return nil
}

func (s *Speaker) Stop(ctx context.Context) error {
	s.mu.Lock()
	srv := s.srv
	s.srv = nil
	s.paths = nil
	s.pairs = nil
	s.owners = nil
	s.mu.Unlock()
	if srv == nil {
		return nil
	}
	err := srv.StopBgp(ctx, &api.StopBgpRequest{})
	srv.Stop()
	return err
}

func (s *Speaker) Advertise(session model.Session) (Advertisement, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.srv == nil {
		return Advertisement{}, fmt.Errorf("BGP speaker is not running")
	}
	if err := s.retryWithdrawalsLocked(); err != nil {
		return Advertisement{}, fmt.Errorf("pending BGP cleanup: %w", err)
	}
	t1, t2, err := BuildSessionPaths(s.cfg, session)
	if err != nil {
		return Advertisement{}, err
	}
	pair := &routePair{paths: []*apiutil.Path{t1, t2}}
	for _, path := range pair.paths {
		if _, exists := s.owners[pathKey(path)]; exists {
			return Advertisement{}, fmt.Errorf("MUP route already owned: %s", pathKey(path))
		}
	}
	s.pairs[pair] = struct{}{}
	for _, path := range pair.paths {
		s.owners[pathKey(path)] = pair
	}
	res, err := s.paths.AddPath(apiutil.AddPathRequest{Paths: pair.paths})
	if err == nil && (len(res) != 2 || res[0].Error != nil || res[1].Error != nil || res[0].UUID == uuid.Nil || res[1].UUID == uuid.Nil) {
		err = fmt.Errorf("BGP did not accept both session paths: %+v", res)
	}
	if err != nil {
		// GoBGP can discard all response UUIDs on a partial failure. Remove
		// exactly this pair by path, never all paths or an entire family.
		pair.pending = true
		if cleanupErr := s.cleanupLocked(pair); cleanupErr != nil {
			return Advertisement{}, errors.Join(err, fmt.Errorf("BGP rollback pending: %w", cleanupErr))
		}
		return Advertisement{}, err
	}
	return Advertisement{T1: res[0].UUID, T2: res[1].UUID, pair: pair}, nil
}

func (s *Speaker) Withdraw(a Advertisement) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.srv == nil {
		return nil
	}
	if _, owned := s.pairs[a.pair]; !owned {
		// Empty, foreign, already withdrawn and prior-Start handles cannot
		// withdraw a later advertisement that reused the same NLRI.
		return nil
	}
	a.pair.pending = true
	return s.cleanupLocked(a.pair)
}

func pathKey(path *apiutil.Path) string {
	return fmt.Sprintf("%d:%s", path.Family, path.Nlri.String())
}

func (s *Speaker) cleanupLocked(pair *routePair) error {
	if err := s.paths.DeletePath(apiutil.DeletePathRequest{Paths: pair.paths}); err != nil {
		return err
	}
	for _, path := range pair.paths {
		delete(s.owners, pathKey(path))
	}
	delete(s.pairs, pair)
	return nil
}

// RetryWithdrawals retains failed cleanup independently of the controller's
// session map, including sessions removed from a later observer snapshot.
func (s *Speaker) RetryWithdrawals() error {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.retryWithdrawalsLocked()
}

func (s *Speaker) retryWithdrawalsLocked() error {
	var errs []error
	for pair := range s.pairs {
		if pair.pending {
			if err := s.cleanupLocked(pair); err != nil {
				errs = append(errs, err)
			}
		}
	}
	return errors.Join(errs...)
}

func (s *Speaker) PeerState(ctx context.Context) string {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.srv == nil {
		return "stopped"
	}
	count, established := 0, 0
	_ = s.srv.ListPeer(ctx, &api.ListPeerRequest{}, func(p *api.Peer) {
		count++
		if p.GetState().GetSessionState() == api.PeerState_SESSION_STATE_ESTABLISHED {
			established++
		}
	})
	return fmt.Sprintf("%d/%d established", established, count)
}

// BuildSessionPaths creates the current draft-ietf-bess-mup-safi-01 wire
// forms used by the GoBGP v4.10 controller and v4.8 PEs. T1 carries the RAN
// F-TEID and QFI; T2 carries the
// UPF F-TEID with endpoint-address-length 64 (IPv4 endpoint + exact TEID).
func BuildSessionPaths(cfg config.Config, s model.Session) (*apiutil.Path, *apiutil.Path, error) {
	if err := s.Validate(); err != nil {
		return nil, nil, err
	}
	rd, err := pkt.ParseRouteDistinguisher(cfg.MUP.SessionRD)
	if err != nil {
		return nil, nil, err
	}
	t1RT, err := pkt.ParseRouteTarget(cfg.MUP.T1RouteTarget)
	if err != nil {
		return nil, nil, err
	}
	t2RT, err := pkt.ParseRouteTarget(cfg.MUP.T2RouteTarget)
	if err != nil {
		return nil, nil, err
	}
	ue := netip.MustParsePrefix(s.UEIPv4 + "/32")
	ran := netip.MustParseAddr(s.RANGTPIPv4)
	upf := netip.MustParseAddr(s.UPFGTPIPv4)
	nextHop, err := netip.ParseAddr(cfg.Controller.NextHop)
	if err != nil {
		return nil, nil, err
	}

	t1NLRI := pkt.NewMUPType1SessionTransformedRoute(rd, ue, teidAddr(s.DownlinkTEID), s.QFI, ran, nil)
	t1, err := finishPath(t1NLRI, nextHop, []pkt.ExtendedCommunityInterface{t1RT})
	if err != nil {
		return nil, nil, err
	}

	endpointLen := uint8(32) + cfg.MUP.TEIDPrefixLength
	t2NLRI := pkt.NewMUPType2SessionTransformedRoute(rd, endpointLen, upf, teidAddr(s.UplinkTEID))
	direct := pkt.NewMUPExtended(pkt.EC_SUBTYPE_MUP_DIRECT_SEG, cfg.MUP.DirectSegmentASN, cfg.MUP.DirectSegmentID)
	t2, err := finishPath(t2NLRI, nextHop, []pkt.ExtendedCommunityInterface{t2RT, direct})
	if err != nil {
		return nil, nil, err
	}
	return t1, t2, nil
}

func finishPath(nlri *pkt.MUPNLRI, nextHop netip.Addr, ecs []pkt.ExtendedCommunityInterface) (*apiutil.Path, error) {
	mp, err := pkt.NewPathAttributeMpReachNLRI(pkt.RF_MUP_IPv4, []pkt.PathNLRI{{NLRI: nlri}}, nextHop)
	if err != nil {
		return nil, err
	}
	attrs := []pkt.PathAttributeInterface{
		pkt.NewPathAttributeOrigin(0),
		pkt.NewPathAttributeExtendedCommunities(ecs),
		mp,
	}
	return &apiutil.Path{Family: pkt.RF_MUP_IPv4, Nlri: nlri, Attrs: attrs}, nil
}

func teidAddr(teid uint32) netip.Addr {
	var b [4]byte
	binary.BigEndian.PutUint32(b[:], teid)
	return netip.AddrFrom4(b)
}
