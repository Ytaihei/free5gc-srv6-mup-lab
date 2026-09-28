package bgp

import (
	"bytes"
	"encoding/hex"
	"fmt"
	"testing"

	"github.com/Ytaihei/free5gc-srv6-mup-lab/internal/model"
	"github.com/osrg/gobgp/v4/pkg/apiutil"
	pkt "github.com/osrg/gobgp/v4/pkg/packet/bgp"
)

// These complete UPDATE fixtures freeze the lab's GoBGP 4.8.0 encoding at
// 5795244, before upgrading the controller. They protect this IPv4 lab's
// compatibility contract, not every MUP route type or draft revision.
func TestMUPWireCompatibility48(t *testing.T) {
	s := model.Session{
		Key: "1:2", CPSEID: 1, UPSEID: 2, UEIPv4: "10.60.0.1", DNN: "internet",
		UPFGTPIPv4: "10.210.32.10", UplinkTEID: 0x11223344,
		RANGTPIPv4: "10.210.31.11", DownlinkTEID: 0xaabbccdd, QFI: 9,
	}
	t1, t2, err := BuildSessionPaths(testConfig(), s)
	if err != nil {
		t.Fatal(err)
	}
	for _, tc := range []struct {
		name string
		path *apiutil.Path
		wire string
	}{
		{"T1", t1, "ffffffffffffffffffffffffffffffff004e020000003740010100c010080002fde800000064800e2500015504c0a87b0e00010003180000fde80000000a200a3c0001aabbccdd09200ad21f0b00"},
		{"T2", t2, "ffffffffffffffffffffffffffffffff004f020000003840010100c010100002fde8000000c80c00fde800000001800e1e00015504c0a87b0e00010004110000fde80000000a400ad2200a11223344"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			want, err := hex.DecodeString(tc.wire)
			if err != nil {
				t.Fatal(err)
			}
			got, err := pkt.NewBGPUpdateMessage(nil, tc.path.Attrs, nil).Serialize()
			if err != nil {
				t.Fatal(err)
			}
			if !bytes.Equal(got, want) {
				t.Fatalf("UPDATE changed from 4.8 fixture:\n got %x\nwant %x", got, want)
			}
			parsed, err := pkt.ParseBGPMessage(want)
			if err != nil {
				t.Fatalf("decode 4.8 UPDATE: %v", err)
			}
			roundTrip, err := parsed.Serialize()
			if err != nil || !bytes.Equal(roundTrip, want) {
				t.Fatalf("UPDATE round trip changed: %x, %v", roundTrip, err)
			}
			update := parsed.Body.(*pkt.BGPUpdate)
			foundReach, foundEC := false, false
			for _, attr := range update.PathAttributes {
				switch a := attr.(type) {
				case *pkt.PathAttributeMpReachNLRI:
					foundReach = true
					if len(a.Value) != 1 {
						t.Fatalf("expected one MUP NLRI, got %d", len(a.Value))
					}
					nlri, ok := a.Value[0].NLRI.(*pkt.MUPNLRI)
					if !ok || nlri.String() != tc.path.Nlri.String() {
						t.Fatalf("decoded MUP fields changed: %v", a.Value)
					}
				case *pkt.PathAttributeExtendedCommunities:
					foundEC = true
					// The exact fixture also binds RTs and the T2 Direct Segment EC.
					if len(a.Value) != map[string]int{"T1": 1, "T2": 2}[tc.name] {
						t.Fatalf("extended communities changed: %v", a.Value)
					}
				}
			}
			if !foundReach || !foundEC {
				t.Fatal("UPDATE lost MP_REACH or extended communities")
			}
		})
	}
}

// GoBGP 4.9 rejects Type 2 endpoint lengths below the IPv4 address size.
// Exercise the complete 8-bit length field, not just the configured /64.
func TestMUPType2IPv4EndpointLengthBoundary(t *testing.T) {
	rd := pkt.NewRouteDistinguisherTwoOctetAS(65000, 10)
	nlri := pkt.NewMUPType2SessionTransformedRoute(rd, 64, teidAddr(0x0ad2200a), teidAddr(0x11223344))
	body, err := nlri.RouteTypeData.Serialize()
	if err != nil {
		t.Fatal(err)
	}
	for bits := 0; bits <= 255; bits++ {
		t.Run(fmt.Sprint(bits), func(t *testing.T) {
			valid := bits >= 32 && bits <= 64
			length := 13 // RD + length + IPv4; no trailing bytes masking a bad length.
			if valid {
				length = 9 + (bits+7)/8
			}
			wire := bytes.Clone(body[:length])
			wire[8] = byte(bits)
			var route pkt.MUPType2SessionTransformedRoute
			err := route.DecodeFromBytes(wire, pkt.AFI_IP)
			if (err == nil) != valid {
				t.Fatalf("endpoint length %d: valid=%t, error=%v", bits, valid, err)
			}
			if valid && (route.EndpointAddress.String() != "10.210.32.10" || route.EndpointAddressLength != uint8(bits)) {
				t.Fatalf("valid endpoint fields changed: %+v", route)
			}
		})
	}
}
