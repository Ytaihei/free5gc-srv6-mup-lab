package dashboard

import (
	"context"
	"strings"
	"testing"
	"time"

	"connectrpc.com/connect"

	mupv1 "github.com/Ytaihei/free5gc-srv6-mup-lab/api/mup/v1"
)

type fakeController struct{}

func (fakeController) Status(context.Context, *connect.Request[mupv1.StatusRequest]) (*connect.Response[mupv1.StatusResponse], error) {
	return connect.NewResponse(&mupv1.StatusResponse{
		ObserverId: "lab-core", Generation: 42, ObserverLastSeenUnixNano: time.Unix(100, 0).UnixNano(),
		ObserverLeaseValid: true, ObservedSessions: 1, SelectedSessions: 1, AdvertisedRoutes: 2,
		BgpState: "2/2 established",
	}), nil
}

func (fakeController) ListSessions(context.Context, *connect.Request[mupv1.ListSessionsRequest]) (*connect.Response[mupv1.ListSessionsResponse], error) {
	return connect.NewResponse(&mupv1.ListSessionsResponse{Sessions: []*mupv1.ControlledSession{{
		Session:  &mupv1.Session{Key: "7:4", UeIpv4: "10.60.0.7", Dnn: "internet", UplinkTeid: 26, DownlinkTeid: 7},
		Selected: true, Advertised: true, Reason: "policy:test:direct",
	}}}), nil
}

type fakeRunner struct{}

func (fakeRunner) Run(_ context.Context, _ string, command string) ([]byte, error) {
	switch {
	case strings.Contains(command, "systemctl is-active"):
		if strings.Contains(command, "systemd-networkd") || strings.Contains(command, "mup-controller") {
			return []byte("active\n"), nil
		}
		return []byte("active\nactive\n"), nil
	case strings.Contains(command, "stats show"):
		return []byte(`[{"name":"RX_PACKETS","packets":10,"bytes":900},{"name":"REDIRECT","packets":8,"bytes":700}]`), nil
	case strings.Contains(command, "mup list"):
		return []byte(`[{"route_type":"isd","rd":"65000:12","srv6_sid":"fd10:1:0:1::"}]`), nil
	case strings.Contains(command, "headend-v4 list"):
		return []byte(`[{"mode":8,"trigger_prefix":"10.60.0.7/32"}]`), nil
	case strings.Contains(command, "ping -I uesimtun0"):
		return []byte("PING 10.210.6.15 (10.210.6.15) from 10.60.0.7 uesimtun0: 56(84) bytes of data.\n64 bytes from 10.210.6.15: icmp_seq=1 ttl=63 time=3.21 ms\n\n--- 10.210.6.15 ping statistics ---\n1 packets transmitted, 1 received, 0% packet loss, time 0ms\nrtt min/avg/max/mdev = 3.210/3.210/3.210/0.000 ms\n"), nil
	default:
		return nil, nil
	}
}

func TestCollectBuildsHealthyMUPState(t *testing.T) {
	collector := NewCollector(fakeController{}, fakeRunner{})
	collector.now = func() time.Time { return time.Unix(105, 0) }
	state := collector.Collect(context.Background())
	if state.Overall != "healthy" {
		t.Fatalf("overall = %q, errors=%v", state.Overall, state.Errors)
	}
	if !state.Paths.MUPActive || state.Paths.FallbackActive {
		t.Fatalf("unexpected paths: %+v", state.Paths)
	}
	if len(state.Nodes) != 6 || len(state.PEs) != 2 || len(state.Sessions) != 1 {
		t.Fatalf("unexpected state sizes: nodes=%d pes=%d sessions=%d", len(state.Nodes), len(state.PEs), len(state.Sessions))
	}
	if state.Controller.ObserverAgeSeconds != 5 {
		t.Fatalf("observer age = %v", state.Controller.ObserverAgeSeconds)
	}
	if !state.UPlane.Success || state.UPlane.Source != "10.60.0.7" || state.UPlane.RTTMilliseconds != 3.210 {
		t.Fatalf("unexpected U-plane probe: %+v", state.UPlane)
	}
	labels := map[string]string{"tpe": "MUP PE (N3/Interwork side)", "npe": "MUP PE (N6/Direct side)"}
	for _, pe := range state.PEs {
		if label, ok := labels[pe.ID]; !ok || pe.Name != label {
			t.Fatalf("PE display name or stable ID changed: %+v", pe)
		}
	}
	for _, node := range state.Nodes {
		if label, ok := labels[node.ID]; ok && (node.Role != label || node.Name != "lab-"+node.ID) {
			t.Fatalf("PE role or compatible VM name changed: %+v", node)
		}
	}
}

func TestParseFailedUPlaneProbe(t *testing.T) {
	probe := parseUPlaneProbe([]byte("1 packets transmitted, 0 received, 100% packet loss\n"), time.Unix(200, 0))
	if probe.Success || probe.Status != "failed" || probe.Error == "" {
		t.Fatalf("unexpected probe: %+v", probe)
	}
}

func TestBGPCounts(t *testing.T) {
	established, total := BGPCounts("2/2 established")
	if established != 2 || total != 2 {
		t.Fatalf("got %d/%d", established, total)
	}
}

func TestPortableCollectorConfig(t *testing.T) {
	cfg := DefaultCollectorConfig()
	cfg.CoreName = "example-core"
	cfg.CoreAddress = "192.0.2.10"
	cfg.DNTarget = "198.51.100.15"
	cfg.IntervalSeconds = 10
	collector := NewCollectorWithConfig(fakeController{}, fakeRunner{}, cfg)
	state := collector.Collect(context.Background())
	if state.Nodes[0].Name != cfg.CoreName || state.Nodes[0].Address != cfg.CoreAddress {
		t.Fatalf("node did not use portable config: %+v", state.Nodes[0])
	}
	if state.UPlane.Target != cfg.DNTarget || state.UPlane.IntervalSeconds != 10 {
		t.Fatalf("probe did not use portable config: %+v", state.UPlane)
	}
}

func TestProbeRejectsInvalidTarget(t *testing.T) {
	cfg := DefaultCollectorConfig()
	cfg.DNTarget = "127.0.0.1; id"
	probe := NewCollectorWithConfig(fakeController{}, fakeRunner{}, cfg).collectUPlaneProbe(context.Background(), time.Now())
	if probe.Success || probe.Error != "DN target must be a literal IPv4 address" {
		t.Fatalf("invalid target was not rejected: %+v", probe)
	}
}
