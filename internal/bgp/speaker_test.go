package bgp

import (
	"testing"
	"time"

	pkt "github.com/osrg/gobgp/v4/pkg/packet/bgp"
	"github.com/Ytaihei/free5gc-srv6-mup-lab/internal/config"
	"github.com/Ytaihei/free5gc-srv6-mup-lab/internal/model"
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
