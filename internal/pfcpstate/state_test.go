package pfcpstate

import (
	"fmt"
	"net"
	"reflect"
	"testing"
	"time"

	"github.com/free5gc/pfcp"
	"github.com/free5gc/pfcp/pfcpType"
)

const (
	smf = "10.100.200.18:8805"
	upf = "10.100.200.102:8805"
)

func TestEstablishmentRequiresAcceptedResponse(t *testing.T) {
	state := New()
	now := time.Unix(100, 0)
	req := establishmentRequest(t, 7)
	changed, err := state.Consume(smf, upf, req, now)
	if err != nil || changed || len(state.Snapshot()) != 0 {
		t.Fatalf("request published state: changed=%v err=%v", changed, err)
	}

	rejected := establishmentResponse(t, 7, pfcpType.CauseRequestRejected)
	changed, err = state.Consume(upf, smf, rejected, now.Add(time.Millisecond))
	if err != nil || changed || len(state.Snapshot()) != 0 {
		t.Fatalf("rejected response published state: changed=%v err=%v", changed, err)
	}

	// A retransmitted request followed by an accepted response is committed.
	_, _ = state.Consume(smf, upf, req, now.Add(time.Second))
	accepted := establishmentResponse(t, 7, pfcpType.CauseRequestAccepted)
	changed, err = state.Consume(upf, smf, accepted, now.Add(2*time.Second))
	if err != nil || !changed {
		t.Fatalf("accepted response: changed=%v err=%v", changed, err)
	}
	sessions := state.Snapshot()
	if len(sessions) != 1 {
		t.Fatalf("sessions = %d", len(sessions))
	}
	s := sessions[0]
	if s.CPSEID != 0x1111 || s.UPSEID != 0x2222 || s.UEIPv4 != "10.60.0.1" || s.DNN != "internet" {
		t.Fatalf("identity = %+v", s)
	}
	if s.UPFGTPIPv4 != "10.210.32.10" || s.UplinkTEID != 0x10203040 || s.RANGTPIPv4 != "10.210.31.11" || s.DownlinkTEID != 0xa0b0c0d0 || s.QFI != 9 {
		t.Fatalf("forwarding = %+v", s)
	}
}

func TestModificationAndDeletionAreTransactional(t *testing.T) {
	state := establishedState(t)
	now := time.Unix(200, 0)
	mod := messageBytes(t, pfcp.Header{Version: 1, S: 1, MessageType: pfcp.PFCP_SESSION_MODIFICATION_REQUEST, SEID: 0x2222, SequenceNumber: 8}, pfcp.PFCPSessionModificationRequest{
		UpdateFAR: []*pfcp.UpdateFAR{{UpdateForwardingParameters: &pfcp.UpdateForwardingParametersIEInFAR{OuterHeaderCreation: &pfcpType.OuterHeaderCreation{
			OuterHeaderCreationDescription: pfcpType.OuterHeaderCreationGtpUUdpIpv4, Teid: 0x01020304, Ipv4Address: net.ParseIP("10.210.31.99").To4(),
		}}}},
	})
	_, err := state.Consume(smf, upf, mod, now)
	if err != nil {
		t.Fatal(err)
	}
	if got := state.Snapshot()[0].DownlinkTEID; got != 0xa0b0c0d0 {
		t.Fatalf("request changed TEID to %#x", got)
	}
	modRsp := messageBytes(t, pfcp.Header{Version: 1, S: 1, MessageType: pfcp.PFCP_SESSION_MODIFICATION_RESPONSE, SEID: 0x1111, SequenceNumber: 8}, pfcp.PFCPSessionModificationResponse{Cause: &pfcpType.Cause{CauseValue: 1}})
	changed, err := state.Consume(upf, smf, modRsp, now.Add(time.Millisecond))
	if err != nil || !changed {
		t.Fatalf("mod response: %v %v", changed, err)
	}
	if got := state.Snapshot()[0].DownlinkTEID; got != 0x01020304 {
		t.Fatalf("committed TEID = %#x", got)
	}

	del := messageBytes(t, pfcp.Header{Version: 1, S: 1, MessageType: pfcp.PFCP_SESSION_DELETION_REQUEST, SEID: 0x2222, SequenceNumber: 9}, pfcp.PFCPSessionDeletionRequest{})
	_, _ = state.Consume(smf, upf, del, now.Add(time.Second))
	if len(state.Snapshot()) != 1 {
		t.Fatal("deletion request removed session")
	}
	delRsp := messageBytes(t, pfcp.Header{Version: 1, S: 1, MessageType: pfcp.PFCP_SESSION_DELETION_RESPONSE, SEID: 0x1111, SequenceNumber: 9}, pfcp.PFCPSessionDeletionResponse{Cause: &pfcpType.Cause{CauseValue: 1}})
	changed, err = state.Consume(upf, smf, delRsp, now.Add(2*time.Second))
	if err != nil || !changed || len(state.Snapshot()) != 0 {
		t.Fatalf("deletion response: changed=%v err=%v sessions=%d", changed, err, len(state.Snapshot()))
	}
}

func TestProvisionalEstablishmentPublishesAfterDownlinkModification(t *testing.T) {
	state := New()
	now := time.Unix(300, 0)
	req := establishmentRequestWithoutRAN(t, 10)
	if changed, err := state.Consume(smf, upf, req, now); err != nil || changed {
		t.Fatalf("provisional request: changed=%v err=%v", changed, err)
	}
	if changed, err := state.Consume(upf, smf, establishmentResponse(t, 10, 1), now.Add(time.Millisecond)); err != nil || changed {
		t.Fatalf("provisional response: changed=%v err=%v", changed, err)
	}
	if got := len(state.Snapshot()); got != 0 {
		t.Fatalf("published %d incomplete sessions", got)
	}

	mod := messageBytes(t, pfcp.Header{Version: 1, S: 1, MessageType: pfcp.PFCP_SESSION_MODIFICATION_REQUEST, SEID: 0x2222, SequenceNumber: 11}, pfcp.PFCPSessionModificationRequest{
		UpdateFAR: []*pfcp.UpdateFAR{{UpdateForwardingParameters: &pfcp.UpdateForwardingParametersIEInFAR{OuterHeaderCreation: &pfcpType.OuterHeaderCreation{
			OuterHeaderCreationDescription: pfcpType.OuterHeaderCreationGtpUUdpIpv4,
			Teid:                           0xa0b0c0d0, Ipv4Address: net.ParseIP("10.210.31.11").To4(),
		}}}},
	})
	if changed, err := state.Consume(smf, upf, mod, now.Add(time.Second)); err != nil || changed {
		t.Fatalf("modification request: changed=%v err=%v", changed, err)
	}
	modRsp := messageBytes(t, pfcp.Header{Version: 1, S: 1, MessageType: pfcp.PFCP_SESSION_MODIFICATION_RESPONSE, SEID: 0x1111, SequenceNumber: 11}, pfcp.PFCPSessionModificationResponse{Cause: &pfcpType.Cause{CauseValue: 1}})
	if changed, err := state.Consume(upf, smf, modRsp, now.Add(2*time.Second)); err != nil || !changed {
		t.Fatalf("modification response: changed=%v err=%v", changed, err)
	}
	if got := state.Snapshot(); len(got) != 1 || got[0].RANGTPIPv4 != "10.210.31.11" {
		t.Fatalf("completed sessions = %+v", got)
	}
}

func TestIncompleteModificationSignalsWithdrawal(t *testing.T) {
	for _, removal := range []struct {
		name string
		body pfcp.PFCPSessionModificationRequest
	}{
		{"pdr", pfcp.PFCPSessionModificationRequest{RemovePDR: []*pfcp.RemovePDR{{PDRID: &pfcpType.PacketDetectionRuleID{RuleId: 1}}}}},
		{"far", pfcp.PFCPSessionModificationRequest{RemoveFAR: []*pfcp.RemoveFAR{{FARID: &pfcpType.FARID{FarIdValue: 1}}}}},
	} {
		for _, published := range []bool{false, true} {
			for _, accepted := range []bool{false, true} {
				t.Run(fmt.Sprintf("%s/published=%v/accepted=%v", removal.name, published, accepted), func(t *testing.T) {
					state := establishedState(t)
					now := time.Unix(400, 0)
					if !published {
						state = New()
						if _, err := state.Consume(smf, upf, establishmentRequestWithoutRAN(t, 20), now); err != nil {
							t.Fatal(err)
						}
						if _, err := state.Consume(upf, smf, establishmentResponse(t, 20, pfcpType.CauseRequestAccepted), now); err != nil {
							t.Fatal(err)
						}
					}
					before := state.Snapshot()
					req := messageBytes(t, pfcp.Header{Version: 1, S: 1, MessageType: pfcp.PFCP_SESSION_MODIFICATION_REQUEST, SEID: 0x2222, SequenceNumber: 21}, removal.body)
					if changed, err := state.Consume(smf, upf, req, now); err != nil || changed {
						t.Fatalf("request must not publish: changed=%v err=%v", changed, err)
					}
					if !reflect.DeepEqual(state.Snapshot(), before) {
						t.Fatal("request mutated the published snapshot")
					}
					cause := uint8(pfcpType.CauseRequestRejected)
					if accepted {
						cause = pfcpType.CauseRequestAccepted
					}
					rsp := messageBytes(t, pfcp.Header{Version: 1, S: 1, MessageType: pfcp.PFCP_SESSION_MODIFICATION_RESPONSE, SEID: 0x1111, SequenceNumber: 21}, pfcp.PFCPSessionModificationResponse{Cause: &pfcpType.Cause{CauseValue: cause}})
					changed, err := state.Consume(upf, smf, rsp, now.Add(time.Millisecond))
					if err != nil || changed != (published && accepted) {
						t.Fatalf("withdrawal signal: changed=%v err=%v", changed, err)
					}
					if accepted {
						if len(state.Snapshot()) != 0 {
							t.Fatal("accepted incomplete state remains published")
						}
					} else if !reflect.DeepEqual(state.Snapshot(), before) {
						t.Fatal("rejected modification changed the published snapshot")
					}
				})
			}
		}
	}
}

func TestNormalizeNetworkInstance(t *testing.T) {
	if got := normalizeNetworkInstance("\x08internet"); got != "internet" {
		t.Fatalf("encoded DNN = %q", got)
	}
	if got := normalizeNetworkInstance("internet"); got != "internet" {
		t.Fatalf("plain DNN = %q", got)
	}
}

func establishedState(t *testing.T) *State {
	t.Helper()
	s := New()
	now := time.Unix(100, 0)
	_, err := s.Consume(smf, upf, establishmentRequest(t, 1), now)
	if err != nil {
		t.Fatal(err)
	}
	_, err = s.Consume(upf, smf, establishmentResponse(t, 1, 1), now)
	if err != nil {
		t.Fatal(err)
	}
	return s
}

func establishmentRequest(t *testing.T, sequence uint32) []byte {
	t.Helper()
	body := pfcp.PFCPSessionEstablishmentRequest{
		CPFSEID: &pfcpType.FSEID{V4: true, Seid: 0x1111, Ipv4Address: net.ParseIP("10.100.200.18").To4()},
		CreatePDR: []*pfcp.CreatePDR{
			{PDI: &pfcp.PDI{NetworkInstance: &pfcpType.NetworkInstance{NetworkInstance: "internet"}, UEIPAddress: &pfcpType.UEIPAddress{V4: true, Ipv4Address: net.ParseIP("10.60.0.1").To4()}, QFI: []*pfcpType.QFI{{QFI: 9}}}},
			{PDI: &pfcp.PDI{LocalFTEID: &pfcpType.FTEID{V4: true, Teid: 0x10203040, Ipv4Address: net.ParseIP("10.210.32.10").To4()}}},
		},
		CreateFAR: []*pfcp.CreateFAR{{ForwardingParameters: &pfcp.ForwardingParametersIEInFAR{OuterHeaderCreation: &pfcpType.OuterHeaderCreation{
			OuterHeaderCreationDescription: pfcpType.OuterHeaderCreationGtpUUdpIpv4, Teid: 0xa0b0c0d0, Ipv4Address: net.ParseIP("10.210.31.11").To4(),
		}}}},
	}
	return messageBytes(t, pfcp.Header{Version: 1, S: 1, MessageType: pfcp.PFCP_SESSION_ESTABLISHMENT_REQUEST, SequenceNumber: sequence}, body)
}

func establishmentRequestWithoutRAN(t *testing.T, sequence uint32) []byte {
	t.Helper()
	body := pfcp.PFCPSessionEstablishmentRequest{
		CPFSEID: &pfcpType.FSEID{V4: true, Seid: 0x1111, Ipv4Address: net.ParseIP("10.100.200.18").To4()},
		CreatePDR: []*pfcp.CreatePDR{
			{PDI: &pfcp.PDI{NetworkInstance: &pfcpType.NetworkInstance{NetworkInstance: "internet"}, UEIPAddress: &pfcpType.UEIPAddress{V4: true, Ipv4Address: net.ParseIP("10.60.0.1").To4()}, QFI: []*pfcpType.QFI{{QFI: 9}}}},
		},
	}
	return messageBytes(t, pfcp.Header{Version: 1, S: 1, MessageType: pfcp.PFCP_SESSION_ESTABLISHMENT_REQUEST, SequenceNumber: sequence}, body)
}

func establishmentResponse(t *testing.T, sequence uint32, cause uint8) []byte {
	t.Helper()
	body := pfcp.PFCPSessionEstablishmentResponse{
		Cause:      &pfcpType.Cause{CauseValue: cause},
		UPFSEID:    &pfcpType.FSEID{V4: true, Seid: 0x2222, Ipv4Address: net.ParseIP("10.100.200.102").To4()},
		CreatedPDR: &pfcp.CreatedPDR{LocalFTEID: &pfcpType.FTEID{V4: true, Teid: 0x10203040, Ipv4Address: net.ParseIP("10.210.32.10").To4()}},
	}
	return messageBytes(t, pfcp.Header{Version: 1, S: 1, MessageType: pfcp.PFCP_SESSION_ESTABLISHMENT_RESPONSE, SEID: 0x1111, SequenceNumber: sequence}, body)
}

func messageBytes(t *testing.T, header pfcp.Header, body any) []byte {
	t.Helper()
	b, err := (&pfcp.Message{Header: header, Body: body}).Marshal()
	if err != nil {
		t.Fatal(err)
	}
	return b
}
