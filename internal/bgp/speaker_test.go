package bgp

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"net"
	"testing"
	"time"

	"github.com/Ytaihei/free5gc-srv6-mup-lab/internal/config"
	"github.com/Ytaihei/free5gc-srv6-mup-lab/internal/model"
	"github.com/google/uuid"
	api "github.com/osrg/gobgp/v4/api"
	"github.com/osrg/gobgp/v4/pkg/apiutil"
	pkt "github.com/osrg/gobgp/v4/pkg/packet/bgp"
	"github.com/osrg/gobgp/v4/pkg/server"
)

func TestBuildSessionPathsDraft01(t *testing.T) {
	cfg := testConfig()
	s := model.Session{
		Key: "1:2", CPSEID: 1, UPSEID: 2, UEIPv4: "10.60.0.1", DNN: "internet",
		UPFGTPIPv4: "10.210.32.10", UplinkTEID: 0x11223344,
		RANGTPIPv4: "10.210.31.11", DownlinkTEID: 0xaabbccdd, QFI: 9,
		ObservedAt: time.Now(),
	}
	t1, t2, err := BuildSessionPaths(cfg, s)
	if err != nil {
		t.Fatal(err)
	}
	t1nlri := t1.Nlri.(*pkt.MUPNLRI)
	r1 := t1nlri.RouteTypeData.(*pkt.MUPType1SessionTransformedRoute)
	if r1.Prefix.String() != "10.60.0.1/32" || r1.EndpointAddress.String() != "10.210.31.11" || r1.QFI != 9 || r1.SourceAddress != nil {
		t.Fatalf("T1 route = %+v", r1)
	}
	if _, err := t1nlri.Serialize(); err != nil {
		t.Fatalf("serialize T1: %v", err)
	}

	t2nlri := t2.Nlri.(*pkt.MUPNLRI)
	r2 := t2nlri.RouteTypeData.(*pkt.MUPType2SessionTransformedRoute)
	if r2.EndpointAddress.String() != "10.210.32.10" || r2.EndpointAddressLength != 64 {
		t.Fatalf("T2 route = %+v", r2)
	}
	foundDirect := false
	for _, a := range t2.Attrs {
		if ext, ok := a.(*pkt.PathAttributeExtendedCommunities); ok {
			for _, ec := range ext.Value {
				if mup, ok := ec.(*pkt.MUPExtended); ok {
					foundDirect = mup.SubType == pkt.EC_SUBTYPE_MUP_DIRECT_SEG && mup.SegmentID2 == 65000 && mup.SegmentID4 == 1
				}
			}
		}
	}
	if !foundDirect {
		t.Fatal("T2 lacks Direct Segment 65000:1 extended community")
	}
	if _, err := t2nlri.Serialize(); err != nil {
		t.Fatalf("serialize T2: %v", err)
	}
}

func testConfig() config.Config {
	return config.Config{
		Controller: config.Controller{LocalASN: 65000, RouterID: "192.168.123.14", ListenPort: -1, NextHop: "192.168.123.14", Lease: 15 * time.Second},
		MUP:        config.MUP{SessionRD: "65000:10", T1RouteTarget: "65000:100", T2RouteTarget: "65000:200", DirectSegmentASN: 65000, DirectSegmentID: 1, TEIDPrefixLength: 32},
		Policy:     []config.PolicyRule{{Name: "internet", DNN: "internet", UEPrefix: "10.60.0.0/16", Action: "direct"}},
	}
}

func routeSession(n byte) model.Session {
	return model.Session{Key: fmt.Sprintf("1:%d", n), CPSEID: 1, UPSEID: uint64(n),
		UEIPv4: fmt.Sprintf("10.60.0.%d", n), DNN: "internet", UPFGTPIPv4: "10.210.32.10",
		UplinkTEID: uint32(n), RANGTPIPv4: "10.210.31.11", DownlinkTEID: uint32(n) + 100, QFI: 9}
}

func startSpeaker(t *testing.T) *Speaker {
	t.Helper()
	s := New(testConfig())
	if err := s.Start(context.Background()); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Stop(context.Background()) })
	return s
}

func rib(t *testing.T, srv *server.BgpServer) map[string]bool {
	t.Helper()
	paths := map[string]bool{}
	if err := srv.ListPath(apiutil.ListPathRequest{TableType: api.TableType_TABLE_TYPE_GLOBAL, Family: pkt.RF_MUP_IPv4},
		func(n pkt.NLRI, p []*apiutil.Path) {
			if len(p) > 0 {
				paths[n.String()] = true
			}
		}); err != nil {
		t.Fatal(err)
	}
	return paths
}

func requireSession(t *testing.T, s *Speaker, srv *server.BgpServer, session model.Session, present bool) {
	t.Helper()
	t1, t2, err := BuildSessionPaths(s.cfg, session)
	if err != nil {
		t.Fatal(err)
	}
	paths := rib(t, srv)
	for _, p := range []*apiutil.Path{t1, t2} {
		if paths[p.Nlri.String()] != present {
			t.Fatalf("route %s present=%v, want %v", p.Nlri, paths[p.Nlri.String()], present)
		}
	}
}

// No tests using the process-global UUID reader run in parallel. Reset it even
// when an assertion fails. This injects an error, not a production frequency claim.
func failUUID(t *testing.T, s *Speaker, available int, session model.Session) error {
	t.Helper()
	uuid.SetRand(bytes.NewReader(make([]byte, available)))
	defer uuid.SetRand(nil)
	a, err := s.Advertise(session)
	if err == nil {
		t.Fatal("expected injected UUID error")
	}
	if a != (Advertisement{}) {
		t.Fatalf("error returned advertisement: %+v", a)
	}
	return err
}

func TestAdvertiseUUIDFailureCleansOnlyAttemptedPair(t *testing.T) {
	for _, available := range []int{0, 16} {
		t.Run(fmt.Sprint(available), func(t *testing.T) {
			s := startSpeaker(t)
			other := routeSession(2)
			a, err := s.Advertise(other)
			if err != nil {
				t.Fatal(err)
			}
			failUUID(t, s, available, routeSession(1))
			requireSession(t, s, s.srv, routeSession(1), false)
			requireSession(t, s, s.srv, other, true)
			if len(s.pairs) != 1 || len(s.owners) != 2 {
				t.Fatal("failed cleanup retained ownership")
			}
			if err := s.Withdraw(a); err != nil {
				t.Fatal(err)
			}
			if len(rib(t, s.srv)) != 0 {
				t.Fatal("normal withdrawal failed")
			}
		})
	}
}

type failingPaths struct {
	pathAPI
	deleteFailures int
	afterDelete    bool
	malformedAdd   bool
}

func (f *failingPaths) AddPath(req apiutil.AddPathRequest) ([]apiutil.AddPathResponse, error) {
	res, err := f.pathAPI.AddPath(req)
	if f.malformedAdd && err == nil {
		return nil, nil
	}
	return res, err
}

func (f *failingPaths) DeletePath(req apiutil.DeletePathRequest) error {
	if len(req.Paths) != 2 || len(req.UUIDs) != 0 || req.DeleteAll || req.DeleteFamily != nil {
		panic("cleanup must be limited to concrete session paths")
	}
	if f.deleteFailures > 0 {
		f.deleteFailures--
		if f.afterDelete {
			if err := f.pathAPI.DeletePath(req); err != nil {
				return err
			}
		}
		return errors.New("injected withdrawal failure")
	}
	return f.pathAPI.DeletePath(req)
}

func TestCleanupRetryAndStaleHandles(t *testing.T) {
	for _, afterDelete := range []bool{false, true} {
		t.Run(fmt.Sprint(afterDelete), func(t *testing.T) {
			s := startSpeaker(t)
			f := &failingPaths{pathAPI: s.srv, deleteFailures: 2, afterDelete: afterDelete}
			s.paths = f
			failUUID(t, s, 16, routeSession(1))
			if len(s.pairs) != 1 || len(s.owners) != 2 {
				t.Fatal("cleanup failure lost ownership")
			}
			if _, err := s.Advertise(routeSession(2)); err == nil {
				t.Fatal("new advertisement bypassed pending cleanup")
			}
			requireSession(t, s, s.srv, routeSession(2), false)
			if err := s.RetryWithdrawals(); err != nil {
				t.Fatal(err)
			}
			if len(rib(t, s.srv)) != 0 || len(s.pairs) != 0 {
				t.Fatal("retry did not clean residual paths")
			}
			a, err := s.Advertise(routeSession(1))
			if err != nil {
				t.Fatal(err)
			}
			f.deleteFailures = 1
			if err := s.Withdraw(a); err == nil {
				t.Fatal("expected withdrawal failure")
			}
			if err := s.RetryWithdrawals(); err != nil {
				t.Fatal(err)
			}
			b, err := s.Advertise(routeSession(1))
			if err != nil {
				t.Fatal(err)
			}
			if err := s.Withdraw(a); err != nil {
				t.Fatal(err)
			}
			requireSession(t, s, s.srv, routeSession(1), true)
			if err := s.Withdraw(b); err != nil {
				t.Fatal(err)
			}
		})
	}
}

func TestOwnershipRejectsOverlapAndPriorGeneration(t *testing.T) {
	s := startSpeaker(t)
	a, err := s.Advertise(routeSession(1))
	if err != nil {
		t.Fatal(err)
	}
	other := routeSession(2)
	other.UplinkTEID = 1 // A distinct UE would still collide with the existing T2.
	if _, err := s.Advertise(other); err == nil {
		t.Fatal("overlapping T2 was accepted")
	}
	requireSession(t, s, s.srv, routeSession(1), true)
	if len(rib(t, s.srv)) != 2 {
		t.Fatal("overlap changed RIB")
	}
	if err := s.Stop(context.Background()); err != nil {
		t.Fatal(err)
	}
	if err := s.Start(context.Background()); err != nil {
		t.Fatal(err)
	}
	if _, err := s.Advertise(routeSession(1)); err != nil {
		t.Fatal(err)
	}
	if err := s.Withdraw(a); err != nil {
		t.Fatal(err)
	}
	requireSession(t, s, s.srv, routeSession(1), true)
}

func TestMalformedAddResponseAlsoRollsBack(t *testing.T) {
	s := startSpeaker(t)
	s.paths = &failingPaths{pathAPI: s.srv, malformedAdd: true}
	if _, err := s.Advertise(routeSession(1)); err == nil {
		t.Fatal("missing response accepted")
	}
	if len(rib(t, s.srv)) != 0 || len(s.pairs) != 0 {
		t.Fatal("malformed response leaked routes")
	}
}

func TestFailureCleanupReachesBothPeers(t *testing.T) {
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	port := listener.Addr().(*net.TCPAddr).Port
	_ = listener.Close()
	cfg := testConfig()
	cfg.Controller.ListenPort = int32(port)
	cfg.Controller.RouterID = "127.0.0.1"
	cfg.Controller.NextHop = "127.0.0.1"
	for _, address := range []string{"127.0.0.2", "127.0.0.3"} {
		cfg.Controller.Peers = append(cfg.Controller.Peers, config.Peer{Address: address, ASN: 65000})
	}
	s := New(cfg)
	ctx := context.Background()
	if err := s.Start(ctx); err != nil {
		t.Fatal(err)
	}
	defer s.Stop(ctx)
	var peers []*server.BgpServer
	for _, address := range []string{"127.0.0.2", "127.0.0.3"} {
		peer := server.NewBgpServer()
		go peer.Serve()
		defer peer.Stop()
		if err := peer.StartBgp(ctx, &api.StartBgpRequest{Global: &api.Global{Asn: 65000, RouterId: address, ListenPort: -1}}); err != nil {
			t.Fatal(err)
		}
		if err := peer.AddPeer(ctx, &api.AddPeerRequest{Peer: &api.Peer{
			Conf:      &api.PeerConf{NeighborAddress: "127.0.0.1", PeerAsn: 65000},
			Transport: &api.Transport{LocalAddress: address, RemotePort: uint32(port)},
			Timers:    &api.Timers{Config: &api.TimersConfig{ConnectRetry: 1}},
			AfiSafis:  []*api.AfiSafi{{Config: &api.AfiSafiConfig{Family: &api.Family{Afi: api.Family_AFI_IP, Safi: api.Family_SAFI_MUP}, Enabled: true}}},
		}}); err != nil {
			t.Fatal(err)
		}
		peers = append(peers, peer)
	}
	wait := func(check func() bool) {
		t.Helper()
		deadline := time.Now().Add(10 * time.Second)
		for time.Now().Before(deadline) {
			if check() {
				return
			}
			time.Sleep(20 * time.Millisecond)
		}
		t.Fatal("BGP convergence timeout")
	}
	wait(func() bool { return s.PeerState(ctx) == "2/2 established" })
	// Delay cleanup once so both peers demonstrably received the failed pair.
	s.paths = &failingPaths{pathAPI: s.srv, deleteFailures: 1}
	failUUID(t, s, 16, routeSession(1))
	wait(func() bool { return len(rib(t, peers[0])) == 2 && len(rib(t, peers[1])) == 2 })
	if err := s.RetryWithdrawals(); err != nil {
		t.Fatal(err)
	}
	// A later good pair is an ordered UPDATE barrier after the withdrawal.
	a, err := s.Advertise(routeSession(2))
	if err != nil {
		t.Fatal(err)
	}
	for _, peer := range peers {
		p1, _, _ := BuildSessionPaths(cfg, routeSession(2))
		wait(func() bool { return rib(t, peer)[p1.Nlri.String()] && len(rib(t, peer)) == 2 })
		requireSession(t, s, peer, routeSession(1), false)
		requireSession(t, s, peer, routeSession(2), true)
	}
	if err := s.Withdraw(a); err != nil {
		t.Fatal(err)
	}
	wait(func() bool { return len(rib(t, peers[0])) == 0 && len(rib(t, peers[1])) == 0 })
}
