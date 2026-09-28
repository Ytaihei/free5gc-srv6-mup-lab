package model

import (
	"fmt"
	"testing"

	mupv1 "github.com/Ytaihei/free5gc-srv6-mup-lab/api/mup/v1"
)

func TestFromProtoQFIRange(t *testing.T) {
	// Cover every valid QFI, the uint8 range, and values that could wrap to
	// valid QFIs if validation occurs after the protobuf uint32 is narrowed.
	values := make([]uint32, 0, 320)
	for qfi := uint32(0); qfi < 320; qfi++ {
		values = append(values, qfi)
	}
	values = append(values, 512, 65536, ^uint32(0))
	for _, qfi := range values {
		t.Run(fmt.Sprint(qfi), func(t *testing.T) {
			p := &mupv1.Session{
				Key: "a:b", CpSeid: 1, UpSeid: 2, UeIpv4: "10.60.0.1", Dnn: "internet",
				UpfGtpIpv4: "10.210.32.10", UplinkTeid: 100,
				RanGtpIpv4: "10.210.31.11", DownlinkTeid: 200, Qfi: qfi,
			}
			s, err := FromProto(p)
			if qfi > 63 {
				if err == nil {
					t.Fatalf("invalid wire QFI %d accepted as %d", qfi, s.QFI)
				}
				return
			}
			if err != nil || uint32(s.QFI) != qfi || s.Proto().Qfi != qfi {
				t.Fatalf("valid QFI %d did not round-trip: session=%+v, err=%v", qfi, s, err)
			}
		})
	}
}

func TestFromProtoNil(t *testing.T) {
	if _, err := FromProto(nil); err == nil {
		t.Fatal("nil session accepted")
	}
}
