package config

import (
	"fmt"
	"net/netip"
	"os"
	"time"

	"gopkg.in/yaml.v3"
)

type Config struct {
	Observer   Observer     `yaml:"observer"`
	Controller Controller   `yaml:"controller"`
	MUP        MUP          `yaml:"mup"`
	Policy     []PolicyRule `yaml:"policy"`
}

type Observer struct {
	ID            string        `yaml:"id"`
	Interface     string        `yaml:"interface"`
	ControllerURL string        `yaml:"controller_url"`
	FullInterval  time.Duration `yaml:"full_interval"`
}

type Controller struct {
	Listen     string        `yaml:"listen"`
	Lease      time.Duration `yaml:"observer_lease"`
	LocalASN   uint32        `yaml:"local_asn"`
	RouterID   string        `yaml:"router_id"`
	ListenPort int32         `yaml:"bgp_listen_port"`
	NextHop    string        `yaml:"next_hop"`
	Peers      []Peer        `yaml:"peers"`
}

type Peer struct {
	Address string `yaml:"address"`
	ASN     uint32 `yaml:"asn"`
}

type MUP struct {
	SessionRD        string `yaml:"session_rd"`
	T1RouteTarget    string `yaml:"t1_route_target"`
	T2RouteTarget    string `yaml:"t2_route_target"`
	DirectSegmentASN uint16 `yaml:"direct_segment_asn"`
	DirectSegmentID  uint32 `yaml:"direct_segment_id"`
	TEIDPrefixLength uint8  `yaml:"teid_prefix_length"`
}

type PolicyRule struct {
	Name       string `yaml:"name"`
	DNN        string `yaml:"dnn"`
	UEPrefix   string `yaml:"ue_prefix"`
	SUPIPrefix string `yaml:"supi_prefix,omitempty"`
	Action     string `yaml:"action"`
}

func Load(path string) (Config, error) {
	b, err := os.ReadFile(path)
	if err != nil {
		return Config{}, err
	}
	var c Config
	if err := yaml.Unmarshal(b, &c); err != nil {
		return Config{}, err
	}
	c.defaults()
	return c, c.Validate()
}

func (c *Config) defaults() {
	if c.Observer.ID == "" {
		c.Observer.ID = "lab-core"
	}
	if c.Observer.Interface == "" {
		c.Observer.Interface = "br-free5gc"
	}
	if c.Observer.FullInterval == 0 {
		c.Observer.FullInterval = 5 * time.Second
	}
	if c.Controller.Listen == "" {
		c.Controller.Listen = "127.0.0.1:9443"
	}
	if c.Controller.Lease == 0 {
		c.Controller.Lease = 15 * time.Second
	}
	if c.Controller.LocalASN == 0 {
		c.Controller.LocalASN = 65000
	}
	if c.Controller.ListenPort == 0 {
		c.Controller.ListenPort = 179
	}
	if c.MUP.TEIDPrefixLength == 0 {
		c.MUP.TEIDPrefixLength = 32
	}
}

func (c Config) Validate() error {
	if c.Observer.FullInterval <= 0 || c.Controller.Lease <= c.Observer.FullInterval {
		return fmt.Errorf("observer lease must be greater than full snapshot interval")
	}
	if c.Controller.LocalASN == 0 || c.Controller.RouterID == "" || c.Controller.NextHop == "" {
		return fmt.Errorf("controller BGP identity and next hop are required")
	}
	if a, err := netip.ParseAddr(c.Controller.RouterID); err != nil || !a.Is4() {
		return fmt.Errorf("router_id must be IPv4")
	}
	if _, err := netip.ParseAddr(c.Controller.NextHop); err != nil {
		return fmt.Errorf("invalid BGP next_hop: %w", err)
	}
	if c.MUP.SessionRD == "" || c.MUP.T1RouteTarget == "" || c.MUP.T2RouteTarget == "" {
		return fmt.Errorf("MUP RD and route targets are required")
	}
	if c.MUP.TEIDPrefixLength > 32 {
		return fmt.Errorf("TEID prefix length must be <= 32")
	}
	for i, r := range c.Policy {
		if r.Action != "direct" && r.Action != "upf" {
			return fmt.Errorf("policy[%d] action must be direct or upf", i)
		}
		if r.UEPrefix != "" {
			if p, err := netip.ParsePrefix(r.UEPrefix); err != nil || !p.Addr().Is4() {
				return fmt.Errorf("policy[%d] has invalid IPv4 UE prefix", i)
			}
		}
	}
	return nil
}
