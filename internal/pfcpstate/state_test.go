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

func TestUnacceptedResponsesDiscardPendingTransaction(t *testing.T) {
	for _, kind := range []string{"establishment", "modification", "deletion"} {
		for _, missingCause := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/missing-cause=%v", kind, missingCause), func(t *testing.T) {
				state := establishedState(t)
				now := time.Unix(500, 0)
				cause := &pfcpType.Cause{CauseValue: pfcpType.CauseRequestRejected}
				if missingCause {
					cause = nil
				}
				var request []byte
				var responseType pfcp.MessageType
				var rejectedBody, acceptedBody any
				switch kind {
				case "establishment":
					state = New()
					request = establishmentRequest(t, 30)
					responseType = pfcp.PFCP_SESSION_ESTABLISHMENT_RESPONSE
					body := pfcp.PFCPSessionEstablishmentResponse{
						Cause: cause,
						UPFSEID: &pfcpType.FSEID{V4: true, Seid: 0x2222,
							Ipv4Address: net.ParseIP("10.100.200.102").To4()},
					}
					rejectedBody = body
					body.Cause = &pfcpType.Cause{CauseValue: pfcpType.CauseRequestAccepted}
					acceptedBody = body
				case "modification":
					request = messageBytes(t, pfcp.Header{Version: 1, S: 1, MessageType: pfcp.PFCP_SESSION_MODIFICATION_REQUEST, SEID: 0x2222, SequenceNumber: 30}, pfcp.PFCPSessionModificationRequest{
						UpdateQER: []*pfcp.UpdateQER{{QoSFlowIdentifier: &pfcpType.QFI{QFI: 7}}},
					})
					responseType = pfcp.PFCP_SESSION_MODIFICATION_RESPONSE
					rejectedBody = pfcp.PFCPSessionModificationResponse{Cause: cause}
					acceptedBody = pfcp.PFCPSessionModificationResponse{Cause: &pfcpType.Cause{CauseValue: pfcpType.CauseRequestAccepted}}
				case "deletion":
					request = messageBytes(t, pfcp.Header{Version: 1, S: 1, MessageType: pfcp.PFCP_SESSION_DELETION_REQUEST, SEID: 0x2222, SequenceNumber: 30}, pfcp.PFCPSessionDeletionRequest{})
					responseType = pfcp.PFCP_SESSION_DELETION_RESPONSE
					rejectedBody = pfcp.PFCPSessionDeletionResponse{Cause: cause}
					acceptedBody = pfcp.PFCPSessionDeletionResponse{Cause: &pfcpType.Cause{CauseValue: pfcpType.CauseRequestAccepted}}
				}
				before := state.Snapshot()
				if kind != "establishment" && len(before) != 1 {
					t.Fatalf("setup sessions = %+v", before)
				}
				if changed, err := state.Consume(smf, upf, request, now); err != nil || changed {
					t.Fatalf("request: changed=%v err=%v", changed, err)
				}
				if got := state.Snapshot(); !reflect.DeepEqual(got, before) {
					t.Fatalf("request changed snapshot: got=%+v want=%+v", got, before)
				}
				header := pfcp.Header{Version: 1, S: 1, MessageType: responseType, SEID: 0x1111, SequenceNumber: 30}
				// An accepted response after rejection cannot revive the consumed request.
				for i, body := range []any{rejectedBody, acceptedBody} {
					if changed, err := state.Consume(upf, smf, messageBytes(t, header, body), now.Add(time.Duration(i+1)*time.Second)); err != nil || changed {
						t.Fatalf("response %d: changed=%v err=%v", i, changed, err)
					}
					if got := state.Snapshot(); !reflect.DeepEqual(got, before) {
						t.Fatalf("response %d changed snapshot: got=%+v want=%+v", i, got, before)
					}
				}
				// A fresh request with the same sequence can still be accepted.
				if changed, err := state.Consume(smf, upf, request, now.Add(3*time.Second)); err != nil || changed {
					t.Fatalf("fresh request: changed=%v err=%v", changed, err)
				}
				committedAt := now.Add(4 * time.Second)
				if changed, err := state.Consume(upf, smf, messageBytes(t, header, acceptedBody), committedAt); err != nil || !changed {
					t.Fatalf("fresh acceptance: changed=%v err=%v", changed, err)
				}
				got := state.Snapshot()
				switch kind {
				case "establishment":
					if len(got) != 1 || got[0].UPSEID != 0x2222 || !got[0].ObservedAt.Equal(committedAt) {
						t.Fatalf("accepted establishment = %+v", got)
					}
				case "modification":
					want := before[0]
					want.QFI, want.ObservedAt = 7, committedAt
					if len(got) != 1 || !reflect.DeepEqual(got[0], want) {
						t.Fatalf("accepted modification: got=%+v want=%+v", got, want)
					}
				case "deletion":
					if len(got) != 0 {
						t.Fatalf("accepted deletion retained sessions: %+v", got)
					}
				}
			})
		}
	}
}

func TestEstablishmentResponseMatching(t *testing.T) {
	for _, mismatch := range []string{"direction", "source", "destination", "sequence", "kind"} {
		t.Run(mismatch, func(t *testing.T) {
			state := New()
			now := time.Unix(600, 0)
			if changed, err := state.Consume(smf, upf, establishmentRequest(t, 40), now); err != nil || changed {
				t.Fatalf("request: changed=%v err=%v", changed, err)
			}
			src, dst := upf, smf
			response := establishmentResponse(t, 40, pfcpType.CauseRequestAccepted)
			switch mismatch {
			case "direction":
				src, dst = smf, upf
			case "source":
				src = "10.100.200.103:8805"
			case "destination":
				dst = "10.100.200.19:8805"
			case "sequence":
				response = establishmentResponse(t, 41, pfcpType.CauseRequestAccepted)
			case "kind":
				response = messageBytes(t, pfcp.Header{Version: 1, S: 1, MessageType: pfcp.PFCP_SESSION_DELETION_RESPONSE, SEID: 0x1111, SequenceNumber: 40}, pfcp.PFCPSessionDeletionResponse{Cause: &pfcpType.Cause{CauseValue: pfcpType.CauseRequestAccepted}})
			}
			if changed, err := state.Consume(src, dst, response, now.Add(time.Second)); err != nil || changed || len(state.Snapshot()) != 0 {
				t.Fatalf("mismatched response: changed=%v err=%v snapshot=%+v", changed, err, state.Snapshot())
			}
			matching := establishmentResponse(t, 40, pfcpType.CauseRequestAccepted)
			if changed, err := state.Consume(upf, smf, matching, now.Add(2*time.Second)); err != nil || !changed || len(state.Snapshot()) != 1 {
				t.Fatalf("matching response: changed=%v err=%v snapshot=%+v", changed, err, state.Snapshot())
			}
			before := state.Snapshot()
			if changed, err := state.Consume(upf, smf, matching, now.Add(3*time.Second)); err != nil || changed {
				t.Fatalf("duplicate response: changed=%v err=%v", changed, err)
			}
			if got := state.Snapshot(); !reflect.DeepEqual(got, before) {
				t.Fatalf("duplicate changed snapshot: got=%+v want=%+v", got, before)
			}
		})
	}
}

func TestEstablishmentPendingExpiryBoundary(t *testing.T) {
	for _, delay := range []time.Duration{30 * time.Second, 30*time.Second + time.Nanosecond} {
		t.Run(delay.String(), func(t *testing.T) {
			state := New()
			now := time.Unix(700, 0)
			if changed, err := state.Consume(smf, upf, establishmentRequest(t, 50), now); err != nil || changed {
				t.Fatalf("request: changed=%v err=%v", changed, err)
			}
			wantChanged := delay == 30*time.Second
			changed, err := state.Consume(upf, smf, establishmentResponse(t, 50, pfcpType.CauseRequestAccepted), now.Add(delay))
			if err != nil || changed != wantChanged {
				t.Fatalf("response after %s: changed=%v want=%v err=%v", delay, changed, wantChanged, err)
			}
			wantCount := 0
			if wantChanged {
				wantCount = 1
			}
			if got := state.Snapshot(); len(got) != wantCount {
				t.Fatalf("response after %s: sessions=%+v want count=%d", delay, got, wantCount)
			}
		})
	}
}

func TestModificationAndDeletionPendingExpiryBoundary(t *testing.T) {
	for _, kind := range []string{"modification", "deletion"} {
		for _, delay := range []time.Duration{30 * time.Second, 30*time.Second + time.Nanosecond} {
			t.Run(kind+"/"+delay.String(), func(t *testing.T) {
				state := establishedState(t)
				before := state.Snapshot()
				if len(before) != 1 {
					t.Fatalf("setup sessions = %+v", before)
				}
				now := time.Unix(800, 0)
				requestHeader := pfcp.Header{Version: 1, S: 1, SEID: 0x2222, SequenceNumber: 60}
				responseHeader := pfcp.Header{Version: 1, S: 1, SEID: 0x1111, SequenceNumber: 60}
				var requestBody, responseBody any
				if kind == "modification" {
					requestHeader.MessageType = pfcp.PFCP_SESSION_MODIFICATION_REQUEST
					responseHeader.MessageType = pfcp.PFCP_SESSION_MODIFICATION_RESPONSE
					requestBody = pfcp.PFCPSessionModificationRequest{
						UpdateQER: []*pfcp.UpdateQER{{QoSFlowIdentifier: &pfcpType.QFI{QFI: 7}}},
					}
					responseBody = pfcp.PFCPSessionModificationResponse{Cause: &pfcpType.Cause{CauseValue: pfcpType.CauseRequestAccepted}}
				} else {
					requestHeader.MessageType = pfcp.PFCP_SESSION_DELETION_REQUEST
					responseHeader.MessageType = pfcp.PFCP_SESSION_DELETION_RESPONSE
					requestBody = pfcp.PFCPSessionDeletionRequest{}
					responseBody = pfcp.PFCPSessionDeletionResponse{Cause: &pfcpType.Cause{CauseValue: pfcpType.CauseRequestAccepted}}
				}
				if changed, err := state.Consume(smf, upf, messageBytes(t, requestHeader, requestBody), now); err != nil || changed {
					t.Fatalf("request: changed=%v err=%v", changed, err)
				}
				if got := state.Snapshot(); !reflect.DeepEqual(got, before) {
					t.Fatalf("request changed snapshot: got=%+v want=%+v", got, before)
				}
				response := messageBytes(t, responseHeader, responseBody)
				responseAt := now.Add(delay)
				wantChanged := delay == 30*time.Second
				if changed, err := state.Consume(upf, smf, response, responseAt); err != nil || changed != wantChanged {
					t.Fatalf("response: changed=%v want=%v err=%v", changed, wantChanged, err)
				}
				want := before
				if wantChanged {
					if kind == "deletion" {
						want = before[:0]
					} else {
						want[0].QFI = 7
						want[0].ObservedAt = responseAt
					}
				}
				if got := state.Snapshot(); !reflect.DeepEqual(got, want) {
					t.Fatalf("response snapshot: got=%+v want=%+v", got, want)
				}
				if changed, err := state.Consume(upf, smf, response, responseAt.Add(time.Second)); err != nil || changed {
					t.Fatalf("duplicate response: changed=%v err=%v", changed, err)
				}
				if got := state.Snapshot(); !reflect.DeepEqual(got, want) {
					t.Fatalf("duplicate changed snapshot: got=%+v want=%+v", got, want)
				}
			})
		}
	}
}
