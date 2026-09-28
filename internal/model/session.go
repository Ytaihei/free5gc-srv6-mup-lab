package model

import (
	"fmt"
	"net/netip"
	"time"

	mupv1 "github.com/Ytaihei/free5gc-srv6-mup-lab/api/mup/v1"
)

// Session is the minimum accepted PFCP state needed to derive IPv4 Direct MUP
// T1ST/T2ST routes. Both tunnel directions must be known before it is eligible.
type Session struct {
	Key          string    `json:"key"`
	CPSEID       uint64    `json:"cp_seid"`
	UPSEID       uint64    `json:"up_seid"`
	UEIPv4       string    `json:"ue_ipv4"`
	DNN          string    `json:"dnn"`
	SUPI         string    `json:"supi,omitempty"`
	UPFGTPIPv4   string    `json:"upf_gtp_ipv4"`
	UplinkTEID   uint32    `json:"uplink_teid"`
	RANGTPIPv4   string    `json:"ran_gtp_ipv4"`
	DownlinkTEID uint32    `json:"downlink_teid"`
	QFI          uint8     `json:"qfi"`
	ObservedAt   time.Time `json:"observed_at"`
}

func (s Session) Validate() error {
	if s.Key == "" || s.CPSEID == 0 || s.UPSEID == 0 {
		return fmt.Errorf("session identity is incomplete")
	}
	for name, raw := range map[string]string{
		"UE IPv4": s.UEIPv4, "UPF GTP IPv4": s.UPFGTPIPv4, "RAN GTP IPv4": s.RANGTPIPv4,
	} {
		a, err := netip.ParseAddr(raw)
		if err != nil || !a.Is4() {
			return fmt.Errorf("%s is not IPv4: %q", name, raw)
		}
	}
	if s.DNN == "" || s.UplinkTEID == 0 || s.DownlinkTEID == 0 {
		return fmt.Errorf("session forwarding state is incomplete")
	}
	if s.QFI > 63 {
		return fmt.Errorf("QFI %d is outside 0..63", s.QFI)
	}
	return nil
}

func (s Session) Proto() *mupv1.Session {
	return &mupv1.Session{
		Key: s.Key, CpSeid: s.CPSEID, UpSeid: s.UPSEID, UeIpv4: s.UEIPv4,
		Dnn: s.DNN, Supi: s.SUPI, UpfGtpIpv4: s.UPFGTPIPv4,
		UplinkTeid: s.UplinkTEID, RanGtpIpv4: s.RANGTPIPv4,
		DownlinkTeid: s.DownlinkTEID, Qfi: uint32(s.QFI),
		ObservedUnixNano: s.ObservedAt.UnixNano(),
	}
}

func FromProto(p *mupv1.Session) (Session, error) {
	if p == nil {
		return Session{}, fmt.Errorf("nil session")
	}
	// Validate the wire value before narrowing it: uint32 values such as 257
	// would otherwise wrap to an apparently valid uint8 QFI.
	if p.Qfi > 63 {
		return Session{}, fmt.Errorf("QFI %d is outside 0..63", p.Qfi)
	}
	s := Session{
		Key: p.Key, CPSEID: p.CpSeid, UPSEID: p.UpSeid, UEIPv4: p.UeIpv4,
		DNN: p.Dnn, SUPI: p.Supi, UPFGTPIPv4: p.UpfGtpIpv4,
		UplinkTEID: p.UplinkTeid, RANGTPIPv4: p.RanGtpIpv4,
		DownlinkTEID: p.DownlinkTeid, QFI: uint8(p.Qfi),
		ObservedAt: time.Unix(0, p.ObservedUnixNano),
	}
	return s, s.Validate()
}
