// Package pfcpstate reconstructs accepted PFCP session state from a passive
// bidirectional packet feed. Requests are staged and become visible only after
// the matching accepted response; rejected and unmatched transactions do not
// mutate published state.
package pfcpstate

import (
	"encoding/hex"
	"fmt"
	"net"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/Ytaihei/free5gc-srv6-mup-lab/internal/model"
	"github.com/free5gc/pfcp"
	"github.com/free5gc/pfcp/pfcpType"
)

type transactionKind uint8

const (
	txnEstablish transactionKind = iota + 1
	txnModify
	txnDelete
)

type pending struct {
	kind         transactionKind
	session      model.Session
	wasPublished bool
	created      time.Time
}

type State struct {
	mu       sync.RWMutex
	sessions map[uint64]model.Session // UP SEID -> session
	pending  map[string]pending
}

func New() *State {
	return &State{sessions: make(map[uint64]model.Session), pending: make(map[string]pending)}
}

// Consume parses one PFCP UDP payload. src and dst are stable endpoint strings
// (normally IP:port); they are used with the 24-bit sequence number to match
// responses even when multiple associations reuse a sequence number.
func (s *State) Consume(src, dst string, payload []byte, now time.Time) (bool, error) {
	var msg pfcp.Message
	if err := msg.Unmarshal(payload); err != nil {
		return false, err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	s.expirePending(now.Add(-30 * time.Second))

	requestKey := txnKey(src, dst, msg.Header.SequenceNumber)
	responseKey := txnKey(dst, src, msg.Header.SequenceNumber)
	switch msg.Header.MessageType {
	case pfcp.PFCP_SESSION_ESTABLISHMENT_REQUEST:
		body := msg.Body.(pfcp.PFCPSessionEstablishmentRequest)
		candidate := model.Session{ObservedAt: now}
		if body.CPFSEID != nil {
			candidate.CPSEID = body.CPFSEID.Seid
		}
		applyCreate(&candidate, body.CreatePDR, body.CreateFAR, body.CreateQER)
		if body.UserID != nil {
			candidate.SUPI = decodeUserID(body.UserID)
		}
		s.pending[requestKey] = pending{kind: txnEstablish, session: candidate, created: now}
		return false, nil

	case pfcp.PFCP_SESSION_ESTABLISHMENT_RESPONSE:
		body := msg.Body.(pfcp.PFCPSessionEstablishmentResponse)
		p, ok := s.pending[responseKey]
		if !ok || p.kind != txnEstablish {
			return false, nil
		}
		delete(s.pending, responseKey)
		if !accepted(body.Cause) {
			return false, nil
		}
		if body.UPFSEID != nil {
			p.session.UPSEID = body.UPFSEID.Seid
		}
		if p.session.CPSEID == 0 {
			p.session.CPSEID = msg.Header.SEID
		}
		applyCreatedPDR(&p.session, body.CreatedPDR)
		p.session.ObservedAt = now
		p.session.Key = fmt.Sprintf("%016x:%016x", p.session.CPSEID, p.session.UPSEID)
		if p.session.UPSEID == 0 {
			return false, fmt.Errorf("accepted establishment has no UP F-SEID")
		}
		s.sessions[p.session.UPSEID] = p.session
		// free5GC establishes the PFCP session before the gNB downlink F-TEID is
		// known, then supplies it in a Session Modification. Retain that
		// provisional state internally, but do not publish it until both tunnel
		// directions validate.
		if err := p.session.Validate(); err != nil {
			return false, nil
		}
		return true, nil

	case pfcp.PFCP_SESSION_MODIFICATION_REQUEST:
		body := msg.Body.(pfcp.PFCPSessionModificationRequest)
		base, ok := s.sessions[msg.Header.SEID]
		if !ok {
			return false, nil
		}
		// Capture publication state before applying a request that may remove
		// forwarding fields. Acceptance must promptly signal its withdrawal.
		wasPublished := base.Validate() == nil
		if len(body.RemovePDR) > 0 {
			base.UPFGTPIPv4, base.UplinkTEID = "", 0
		}
		if len(body.RemoveFAR) > 0 {
			base.RANGTPIPv4, base.DownlinkTEID = "", 0
		}
		applyCreate(&base, body.CreatePDR, body.CreateFAR, body.CreateQER)
		applyUpdate(&base, body.UpdatePDR, body.UpdateFAR, body.UpdateQER)
		base.ObservedAt = now
		s.pending[requestKey] = pending{kind: txnModify, session: base, wasPublished: wasPublished, created: now}
		return false, nil

	case pfcp.PFCP_SESSION_MODIFICATION_RESPONSE:
		body := msg.Body.(pfcp.PFCPSessionModificationResponse)
		p, ok := s.pending[responseKey]
		if !ok || p.kind != txnModify {
			return false, nil
		}
		delete(s.pending, responseKey)
		if !accepted(body.Cause) {
			return false, nil
		}
		applyCreatedPDR(&p.session, body.CreatedPDR)
		p.session.ObservedAt = now
		if err := p.session.Validate(); err != nil {
			delete(s.sessions, p.session.UPSEID)
			return p.wasPublished, nil // fail closed: withdraw only if it was published
		}
		s.sessions[p.session.UPSEID] = p.session
		return true, nil

	case pfcp.PFCP_SESSION_DELETION_REQUEST:
		base, ok := s.sessions[msg.Header.SEID]
		if ok {
			s.pending[requestKey] = pending{kind: txnDelete, session: base, wasPublished: base.Validate() == nil, created: now}
		}
		return false, nil

	case pfcp.PFCP_SESSION_DELETION_RESPONSE:
		body := msg.Body.(pfcp.PFCPSessionDeletionResponse)
		p, ok := s.pending[responseKey]
		if !ok || p.kind != txnDelete {
			return false, nil
		}
		delete(s.pending, responseKey)
		if !accepted(body.Cause) {
			return false, nil
		}
		delete(s.sessions, p.session.UPSEID)
		return p.wasPublished, nil
	default:
		return false, nil
	}
}

func (s *State) Snapshot() []model.Session {
	s.mu.RLock()
	defer s.mu.RUnlock()
	out := make([]model.Session, 0, len(s.sessions))
	for _, session := range s.sessions {
		if session.Validate() == nil {
			out = append(out, session)
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Key < out[j].Key })
	return out
}

func (s *State) expirePending(before time.Time) {
	for key, p := range s.pending {
		if p.created.Before(before) {
			delete(s.pending, key)
		}
	}
}

func txnKey(src, dst string, seq uint32) string { return fmt.Sprintf("%s>%s#%06x", src, dst, seq) }
func accepted(c *pfcpType.Cause) bool {
	return c != nil && c.CauseValue == pfcpType.CauseRequestAccepted
}

func applyCreate(s *model.Session, pdrs []*pfcp.CreatePDR, fars []*pfcp.CreateFAR, qers []*pfcp.CreateQER) {
	for _, p := range pdrs {
		if p != nil {
			applyPDI(s, p.PDI)
		}
	}
	for _, f := range fars {
		if f != nil && f.ForwardingParameters != nil {
			applyOHC(s, f.ForwardingParameters.OuterHeaderCreation)
		}
	}
	for _, q := range qers {
		if q != nil && q.QoSFlowIdentifier != nil {
			s.QFI = q.QoSFlowIdentifier.QFI
		}
	}
}

func applyUpdate(s *model.Session, pdrs []*pfcp.UpdatePDR, fars []*pfcp.UpdateFAR, qers []*pfcp.UpdateQER) {
	for _, p := range pdrs {
		if p != nil {
			applyPDI(s, p.PDI)
		}
	}
	for _, f := range fars {
		if f != nil && f.UpdateForwardingParameters != nil {
			applyOHC(s, f.UpdateForwardingParameters.OuterHeaderCreation)
		}
	}
	for _, q := range qers {
		if q != nil && q.QoSFlowIdentifier != nil {
			s.QFI = q.QoSFlowIdentifier.QFI
		}
	}
}

func applyPDI(s *model.Session, p *pfcp.PDI) {
	if p == nil {
		return
	}
	if p.NetworkInstance != nil && p.NetworkInstance.NetworkInstance != "" {
		s.DNN = normalizeNetworkInstance(p.NetworkInstance.NetworkInstance)
	}
	if p.UEIPAddress != nil && p.UEIPAddress.V4 {
		s.UEIPv4 = ipv4(p.UEIPAddress.Ipv4Address)
	}
	if p.LocalFTEID != nil && !p.LocalFTEID.Ch && p.LocalFTEID.V4 {
		s.UPFGTPIPv4, s.UplinkTEID = ipv4(p.LocalFTEID.Ipv4Address), p.LocalFTEID.Teid
	}
	if len(p.QFI) > 0 && p.QFI[0] != nil {
		s.QFI = p.QFI[0].QFI
	}
}

func normalizeNetworkInstance(raw string) string {
	b := []byte(strings.TrimSuffix(raw, "."))
	if len(b) == 0 || b[0] >= 32 {
		return string(b)
	}
	labels := make([]string, 0, 3)
	for len(b) > 0 {
		n := int(b[0])
		b = b[1:]
		if n == 0 {
			break
		}
		if n > 63 || n > len(b) {
			return raw
		}
		labels = append(labels, string(b[:n]))
		b = b[n:]
	}
	if len(labels) == 0 || len(b) != 0 {
		return raw
	}
	return strings.Join(labels, ".")
}

func applyOHC(s *model.Session, o *pfcpType.OuterHeaderCreation) {
	if o == nil || o.OuterHeaderCreationDescription&pfcpType.OuterHeaderCreationGtpUUdpIpv4 == 0 {
		return
	}
	s.RANGTPIPv4, s.DownlinkTEID = ipv4(o.Ipv4Address), o.Teid
}

func applyCreatedPDR(s *model.Session, p *pfcp.CreatedPDR) {
	if p == nil || p.LocalFTEID == nil || !p.LocalFTEID.V4 {
		return
	}
	s.UPFGTPIPv4, s.UplinkTEID = ipv4(p.LocalFTEID.Ipv4Address), p.LocalFTEID.Teid
}

func ipv4(ip net.IP) string {
	if v := ip.To4(); v != nil {
		return v.String()
	}
	return ""
}

// User ID is optional for policy. free5GC's library exposes its raw encoding;
// return a stable representation and decode the common IMSI flag/length form.
func decodeUserID(u *pfcpType.UserID) string {
	b := u.UserIDdata
	if len(b) >= 3 && b[0]&1 != 0 {
		n := int(b[1])
		if 2+n <= len(b) {
			var out strings.Builder
			for _, x := range b[2 : 2+n] {
				out.WriteByte('0' + (x & 0x0f))
				if x>>4 != 0x0f {
					out.WriteByte('0' + (x >> 4))
				}
			}
			return out.String()
		}
	}
	return hex.EncodeToString(b)
}
