package bgp

import (
	"context"
	"encoding/binary"
	"fmt"
	"net/netip"
	"sync"

	"github.com/google/uuid"
	api "github.com/osrg/gobgp/v4/api"
	"github.com/osrg/gobgp/v4/pkg/apiutil"
	pkt "github.com/osrg/gobgp/v4/pkg/packet/bgp"
	"github.com/osrg/gobgp/v4/pkg/server"
	"github.com/Ytaihei/free5gc-srv6-mup-lab/internal/config"
	"github.com/Ytaihei/free5gc-srv6-mup-lab/internal/model"
)

type Advertisement struct {
	T1 uuid.UUID
	T2 uuid.UUID
}

type Speaker struct {
	mu  sync.Mutex
	srv *server.BgpServer
	cfg config.Config
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
	return nil
}

func (s *Speaker) Stop(ctx context.Context) error {
	s.mu.Lock()
	srv := s.srv
	s.srv = nil
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
	t1, t2, err := BuildSessionPaths(s.cfg, session)
	if err != nil {
		return Advertisement{}, err
	}
	res, err := s.srv.AddPath(apiutil.AddPathRequest{Paths: []*apiutil.Path{t1, t2}})
	if err != nil {
		return Advertisement{}, err
	}
	if len(res) != 2 || res[0].Error != nil || res[1].Error != nil {
		ids := make([]uuid.UUID, 0, 2)
		for _, r := range res {
			if r.Error == nil && r.UUID != uuid.Nil {
				ids = append(ids, r.UUID)
			}
		}
		if len(ids) > 0 {
			_ = s.srv.DeletePath(apiutil.DeletePathRequest{UUIDs: ids})
		}
		return Advertisement{}, fmt.Errorf("BGP did not accept both session paths: %+v", res)
	}
	return Advertisement{T1: res[0].UUID, T2: res[1].UUID}, nil
}

func (s *Speaker) Withdraw(a Advertisement) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.srv == nil {
		return nil
	}
	ids := make([]uuid.UUID, 0, 2)
	if a.T1 != uuid.Nil {
		ids = append(ids, a.T1)
	}
	if a.T2 != uuid.Nil {
		ids = append(ids, a.T2)
	}
	if len(ids) == 0 {
		return nil
	}
	return s.srv.DeletePath(apiutil.DeletePathRequest{UUIDs: ids})
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
// forms used by the GoBGP v4.9 controller and v4.8 PEs. T1 carries the RAN
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
