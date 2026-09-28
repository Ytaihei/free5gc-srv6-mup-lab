package dashboard

import "time"

type State struct {
	Stale      bool             `json:"stale"`
	UpdatedAt  time.Time        `json:"updated_at"`
	DurationMS int64            `json:"collection_duration_ms"`
	Overall    string           `json:"overall"`
	Controller ControllerStatus `json:"controller"`
	Sessions   []Session        `json:"sessions"`
	Nodes      []Node           `json:"nodes"`
	PEs        []PEState        `json:"pes"`
	Paths      PathState        `json:"paths"`
	UPlane     UPlaneProbe      `json:"uplane_probe"`
	Errors     []string         `json:"errors"`
}

type ControllerStatus struct {
	Available          bool      `json:"available"`
	ObserverID         string    `json:"observer_id"`
	Generation         uint64    `json:"generation"`
	ObserverLastSeen   time.Time `json:"observer_last_seen,omitempty"`
	ObserverAgeSeconds float64   `json:"observer_age_seconds"`
	ObserverLeaseValid bool      `json:"observer_lease_valid"`
	ObservedSessions   uint32    `json:"observed_sessions"`
	SelectedSessions   uint32    `json:"selected_sessions"`
	AdvertisedRoutes   uint32    `json:"advertised_routes"`
	BGPState           string    `json:"bgp_state"`
}

type Session struct {
	Key          string    `json:"key"`
	CPSEID       uint64    `json:"cp_seid"`
	UPSEID       uint64    `json:"up_seid"`
	UEIPv4       string    `json:"ue_ipv4"`
	DNN          string    `json:"dnn"`
	SUPI         string    `json:"supi"`
	UPFGTPIPv4   string    `json:"upf_gtp_ipv4"`
	UplinkTEID   uint32    `json:"uplink_teid"`
	RANGTPIPv4   string    `json:"ran_gtp_ipv4"`
	DownlinkTEID uint32    `json:"downlink_teid"`
	QFI          uint32    `json:"qfi"`
	ObservedAt   time.Time `json:"observed_at,omitempty"`
	Selected     bool      `json:"selected"`
	Suppressed   bool      `json:"suppressed"`
	Reason       string    `json:"reason"`
	Advertised   bool      `json:"advertised"`
}

type Node struct {
	ID        string    `json:"id"`
	Name      string    `json:"name"`
	Role      string    `json:"role"`
	Address   string    `json:"address"`
	Reachable bool      `json:"reachable"`
	Healthy   bool      `json:"healthy"`
	Services  []Service `json:"services"`
}

type Service struct {
	Name   string `json:"name"`
	Active bool   `json:"active"`
	State  string `json:"state"`
}

type PEState struct {
	ID       string       `json:"id"`
	Name     string       `json:"name"`
	Stats    []PacketStat `json:"stats"`
	Routes   []MUPRoute   `json:"routes"`
	Headends []Headend    `json:"headends"`
}

type PacketStat struct {
	Name    string `json:"name"`
	Packets uint64 `json:"packets"`
	Bytes   uint64 `json:"bytes"`
}

type MUPRoute struct {
	RouteType    string   `json:"route_type"`
	RD           string   `json:"rd"`
	RouteTargets []string `json:"route_targets"`
	Prefix       string   `json:"prefix,omitempty"`
	Address      string   `json:"address,omitempty"`
	TEIDLength   uint32   `json:"teid_len"`
	SegmentID2   uint32   `json:"segment_id2,omitempty"`
	SegmentID4   uint32   `json:"segment_id4,omitempty"`
	SRv6SID      string   `json:"srv6_sid"`
	NextHop      string   `json:"next_hop"`
}

type Headend struct {
	Mode          int      `json:"mode"`
	TriggerPrefix string   `json:"trigger_prefix"`
	SourceAddress string   `json:"src_addr"`
	DestAddress   string   `json:"dst_addr"`
	Segments      []string `json:"segments,omitempty"`
}

type PathState struct {
	MUPActive      bool `json:"mup_active"`
	FallbackActive bool `json:"fallback_active"`
}

type UPlaneProbe struct {
	Status          string    `json:"status"`
	Success         bool      `json:"success"`
	Source          string    `json:"source,omitempty"`
	Target          string    `json:"target"`
	RTTMilliseconds float64   `json:"rtt_ms"`
	CheckedAt       time.Time `json:"checked_at"`
	IntervalSeconds int       `json:"interval_seconds"`
	Error           string    `json:"error,omitempty"`
}
