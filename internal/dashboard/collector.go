package dashboard

import (
	"context"
	"encoding/json"
	"fmt"
	"net/netip"
	"os/exec"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"time"

	"connectrpc.com/connect"

	mupv1 "github.com/Ytaihei/free5gc-srv6-mup-lab/api/mup/v1"
)

const vinberoJSON = "sudo env VINBERO_SERVER=http://127.0.0.1:8080 /usr/local/bin/vinbero --json "

var (
	probeSourcePattern = regexp.MustCompile(`(?m)^PING .+ from ([0-9.]+) `)
	probeRTTPattern    = regexp.MustCompile(`(?m)^rtt .+ = [0-9.]+/([0-9.]+)/`)
)

type controllerClient interface {
	Status(context.Context, *connect.Request[mupv1.StatusRequest]) (*connect.Response[mupv1.StatusResponse], error)
	ListSessions(context.Context, *connect.Request[mupv1.ListSessionsRequest]) (*connect.Response[mupv1.ListSessionsResponse], error)
}

type remoteRunner interface {
	Run(context.Context, string, string) ([]byte, error)
}

type SSHRunner struct {
	User         string
	IdentityFile string
}

func (r SSHRunner) Run(ctx context.Context, host, command string) ([]byte, error) {
	user := r.User
	if user == "" {
		user = "ubuntu"
	}
	if !regexp.MustCompile(`^[a-z_][a-z0-9_-]*$`).MatchString(user) {
		return nil, fmt.Errorf("invalid SSH user")
	}
	if address, err := netip.ParseAddr(host); err != nil || !address.Is4() {
		return nil, fmt.Errorf("SSH target must be a literal IPv4 address")
	}
	args := []string{
		"-o", "BatchMode=yes",
		"-o", "ConnectTimeout=3",
		"-o", "StrictHostKeyChecking=accept-new",
		"-o", "ControlMaster=auto",
		"-o", "ControlPersist=60",
		"-o", "ControlPath=/tmp/srv6-mup-dashboard-%C",
	}
	if r.IdentityFile != "" {
		args = append(args, "-i", r.IdentityFile)
	}
	args = append(args, user+"@"+host, command)
	// Only stdout belongs to the remote command. OpenSSH multiplexing notices
	// are emitted on stderr and must never be mistaken for service state/JSON.
	return exec.CommandContext(ctx, "ssh", args...).Output()
}

type nodeSpec struct {
	ID       string
	Name     string
	Role     string
	Address  string
	Services []string
}

type CollectorConfig struct {
	CoreName        string
	RANName         string
	TPEName         string
	NPEName         string
	MUPCName        string
	DNName          string
	CoreAddress     string
	RANAddress      string
	TPEAddress      string
	NPEAddress      string
	MUPCAddress     string
	DNAddress       string
	DNTarget        string
	IntervalSeconds int
}

func DefaultCollectorConfig() CollectorConfig {
	return CollectorConfig{
		CoreName: "lab-core", RANName: "lab-ran", TPEName: "lab-tpe",
		NPEName: "lab-npe", MUPCName: "lab-mupc", DNName: "lab-dn",
		CoreAddress: "192.168.123.10", RANAddress: "192.168.123.11",
		TPEAddress: "192.168.123.12", NPEAddress: "192.168.123.13",
		MUPCAddress: "192.168.123.14", DNAddress: "192.168.123.15",
		DNTarget: "10.210.6.15",
	}
}

type Collector struct {
	controller controllerClient
	runner     remoteRunner
	config     CollectorConfig
	now        func() time.Time
}

func NewCollector(controller controllerClient, runner remoteRunner) *Collector {
	return NewCollectorWithConfig(controller, runner, DefaultCollectorConfig())
}

func NewCollectorWithConfig(controller controllerClient, runner remoteRunner, config CollectorConfig) *Collector {
	if config.IntervalSeconds <= 0 {
		config.IntervalSeconds = 5
	}
	return &Collector{controller: controller, runner: runner, config: config, now: time.Now}
}

func (c *Collector) labNodes() []nodeSpec {
	return []nodeSpec{
		{ID: "core", Name: c.config.CoreName, Role: "5GC + UPF + N4 observer", Address: c.config.CoreAddress, Services: []string{"free5gc-lab", "pfcp-observer"}},
		{ID: "ran", Name: c.config.RANName, Role: "UERANSIM gNB + UE", Address: c.config.RANAddress, Services: []string{"ueransim-gnb", "ueransim-ue"}},
		{ID: "tpe", Name: c.config.TPEName, Role: "MUP PE (N3/Interwork side)", Address: c.config.TPEAddress, Services: []string{"vinbero", "vinbero-bootstrap"}},
		{ID: "npe", Name: c.config.NPEName, Role: "MUP PE (N6/Direct side)", Address: c.config.NPEAddress, Services: []string{"vinbero", "vinbero-bootstrap"}},
		{ID: "mupc", Name: c.config.MUPCName, Role: "MUP-C + BGP RR", Address: c.config.MUPCAddress, Services: []string{"mup-controller"}},
		{ID: "dn", Name: c.config.DNName, Role: "Data network", Address: c.config.DNAddress, Services: []string{"systemd-networkd"}},
	}
}

func (c *Collector) uplaneProbeCommand() string {
	return fmt.Sprintf("ping -I uesimtun0 -c 1 -W 1 -n %s", c.config.DNTarget)
}

func (c *Collector) Collect(ctx context.Context) State {
	started := c.now()
	labNodes := c.labNodes()
	state := State{UpdatedAt: started, Nodes: make([]Node, len(labNodes)), PEs: make([]PEState, 2), Errors: []string{}}
	var mu sync.Mutex
	var wg sync.WaitGroup

	addError := func(err error) {
		if err == nil {
			return
		}
		mu.Lock()
		state.Errors = append(state.Errors, err.Error())
		mu.Unlock()
	}

	wg.Add(1)
	go func() {
		defer wg.Done()
		status, sessions, errs := c.collectController(ctx, started)
		mu.Lock()
		state.Controller = status
		state.Sessions = sessions
		mu.Unlock()
		for _, err := range errs {
			addError(err)
		}
	}()

	for i, spec := range labNodes {
		i, spec := i, spec
		wg.Add(1)
		go func() {
			defer wg.Done()
			node := c.collectNode(ctx, spec)
			mu.Lock()
			state.Nodes[i] = node
			mu.Unlock()
			if !node.Reachable {
				addError(fmt.Errorf("%s is unreachable", spec.Name))
			}
		}()
	}

	for i, spec := range []struct{ id, name, host string }{{"tpe", "MUP PE (N3/Interwork side)", c.config.TPEAddress}, {"npe", "MUP PE (N6/Direct side)", c.config.NPEAddress}} {
		i, spec := i, spec
		wg.Add(1)
		go func() {
			defer wg.Done()
			pe, errs := c.collectPE(ctx, spec.id, spec.name, spec.host)
			mu.Lock()
			state.PEs[i] = pe
			mu.Unlock()
			for _, err := range errs {
				addError(err)
			}
		}()
	}

	wg.Add(1)
	go func() {
		defer wg.Done()
		probe := c.collectUPlaneProbe(ctx, started)
		mu.Lock()
		state.UPlane = probe
		mu.Unlock()
	}()

	wg.Wait()
	state.UpdatedAt = c.now()
	state.DurationMS = state.UpdatedAt.Sub(started).Milliseconds()
	state.Paths.MUPActive = state.Controller.ObserverLeaseValid && state.Controller.AdvertisedRoutes >= 2 && anyAdvertised(state.Sessions)
	state.Paths.FallbackActive = !state.Paths.MUPActive
	state.Overall = overallStatus(state)
	return state
}

func (c *Collector) collectController(ctx context.Context, now time.Time) (ControllerStatus, []Session, []error) {
	var errs []error
	statusResponse, err := c.controller.Status(ctx, connect.NewRequest(&mupv1.StatusRequest{}))
	if err != nil {
		errs = append(errs, fmt.Errorf("MUP-C status: %w", err))
		return ControllerStatus{}, nil, errs
	}
	msg := statusResponse.Msg
	status := ControllerStatus{
		Available:          true,
		ObserverID:         msg.ObserverId,
		Generation:         msg.Generation,
		ObserverLeaseValid: msg.ObserverLeaseValid,
		ObservedSessions:   msg.ObservedSessions,
		SelectedSessions:   msg.SelectedSessions,
		AdvertisedRoutes:   msg.AdvertisedRoutes,
		BGPState:           msg.BgpState,
	}
	if msg.ObserverLastSeenUnixNano > 0 {
		status.ObserverLastSeen = time.Unix(0, msg.ObserverLastSeenUnixNano)
		status.ObserverAgeSeconds = max(0, now.Sub(status.ObserverLastSeen).Seconds())
	}

	sessionResponse, err := c.controller.ListSessions(ctx, connect.NewRequest(&mupv1.ListSessionsRequest{}))
	if err != nil {
		errs = append(errs, fmt.Errorf("MUP-C sessions: %w", err))
		return status, nil, errs
	}
	sessions := make([]Session, 0, len(sessionResponse.Msg.Sessions))
	for _, controlled := range sessionResponse.Msg.Sessions {
		s := controlled.Session
		if s == nil {
			continue
		}
		item := Session{
			Key: s.Key, CPSEID: s.CpSeid, UPSEID: s.UpSeid, UEIPv4: s.UeIpv4, DNN: s.Dnn, SUPI: s.Supi,
			UPFGTPIPv4: s.UpfGtpIpv4, UplinkTEID: s.UplinkTeid, RANGTPIPv4: s.RanGtpIpv4,
			DownlinkTEID: s.DownlinkTeid, QFI: s.Qfi, Selected: controlled.Selected,
			Suppressed: controlled.Suppressed, Reason: controlled.Reason, Advertised: controlled.Advertised,
		}
		if s.ObservedUnixNano > 0 {
			item.ObservedAt = time.Unix(0, s.ObservedUnixNano)
		}
		sessions = append(sessions, item)
	}
	return status, sessions, errs
}

func (c *Collector) collectNode(ctx context.Context, spec nodeSpec) Node {
	node := Node{ID: spec.ID, Name: spec.Name, Role: spec.Role, Address: spec.Address}
	command := "systemctl is-active " + strings.Join(spec.Services, " ")
	out, err := c.runner.Run(ctx, spec.Address, command)
	lines := strings.Fields(string(out))
	if len(lines) == 0 && err != nil {
		for _, name := range spec.Services {
			node.Services = append(node.Services, Service{Name: name, State: "unreachable"})
		}
		return node
	}
	node.Reachable = true
	node.Healthy = len(lines) >= len(spec.Services)
	for i, name := range spec.Services {
		serviceState := "unknown"
		if i < len(lines) {
			serviceState = lines[i]
		}
		active := serviceState == "active"
		node.Services = append(node.Services, Service{Name: name, State: serviceState, Active: active})
		node.Healthy = node.Healthy && active
	}
	return node
}

func (c *Collector) collectPE(ctx context.Context, id, name, host string) (PEState, []error) {
	pe := PEState{ID: id, Name: name}
	var errs []error
	queries := []struct {
		name    string
		command string
		target  any
	}{
		{"stats", vinberoJSON + "stats show", &pe.Stats},
		{"MUP routes", vinberoJSON + "mup list", &pe.Routes},
		{"headends", vinberoJSON + "headend-v4 list", &pe.Headends},
	}
	for _, query := range queries {
		out, err := c.runner.Run(ctx, host, query.command)
		if err != nil {
			errs = append(errs, fmt.Errorf("%s %s: %w", name, query.name, err))
			continue
		}
		if err := json.Unmarshal(out, query.target); err != nil {
			errs = append(errs, fmt.Errorf("%s %s JSON: %w", name, query.name, err))
		}
	}
	return pe, errs
}

func (c *Collector) collectUPlaneProbe(ctx context.Context, checkedAt time.Time) UPlaneProbe {
	if address, err := netip.ParseAddr(c.config.DNTarget); err != nil || !address.Is4() {
		return UPlaneProbe{Status: "failed", CheckedAt: checkedAt, Error: "DN target must be a literal IPv4 address"}
	}
	out, err := c.runner.Run(ctx, c.config.RANAddress, c.uplaneProbeCommand())
	probe := parseUPlaneProbe(out, checkedAt)
	probe.Target = c.config.DNTarget
	probe.IntervalSeconds = c.config.IntervalSeconds
	if err != nil {
		probe.Status = "failed"
		probe.Success = false
		if probe.Error == "" {
			probe.Error = "ICMP timeout or UE tunnel unavailable"
		}
	}
	return probe
}

func parseUPlaneProbe(output []byte, checkedAt time.Time) UPlaneProbe {
	probe := UPlaneProbe{
		Status:          "failed",
		Target:          "10.210.6.15",
		CheckedAt:       checkedAt,
		IntervalSeconds: 5,
	}
	if match := probeSourcePattern.FindSubmatch(output); len(match) == 2 {
		probe.Source = string(match[1])
	}
	match := probeRTTPattern.FindSubmatch(output)
	if len(match) != 2 {
		probe.Error = "ICMP reply was not received"
		return probe
	}
	rtt, err := strconv.ParseFloat(string(match[1]), 64)
	if err != nil {
		probe.Error = "invalid ICMP RTT"
		return probe
	}
	probe.Status = "ok"
	probe.Success = true
	probe.RTTMilliseconds = rtt
	return probe
}

func anyAdvertised(sessions []Session) bool {
	for _, session := range sessions {
		if session.Advertised {
			return true
		}
	}
	return false
}

func overallStatus(state State) string {
	if !state.Controller.Available {
		return "offline"
	}
	for _, node := range state.Nodes {
		if !node.Reachable {
			return "offline"
		}
		if !node.Healthy {
			return "degraded"
		}
	}
	if !state.Controller.ObserverLeaseValid || !strings.Contains(state.Controller.BGPState, "2/2") || len(state.Errors) > 0 {
		return "degraded"
	}
	if state.UPlane.Status == "failed" {
		return "degraded"
	}
	return "healthy"
}

func BGPCounts(state string) (established, total int) {
	parts := strings.Fields(state)
	if len(parts) == 0 {
		return 0, 0
	}
	counts := strings.Split(parts[0], "/")
	if len(counts) != 2 {
		return 0, 0
	}
	established, _ = strconv.Atoi(counts[0])
	total, _ = strconv.Atoi(counts[1])
	return established, total
}
