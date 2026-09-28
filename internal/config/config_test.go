package config

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestLoadDurationsAndValidate(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "config.yml")
	data := []byte(`
observer:
  controller_url: http://192.0.2.14:9443
  full_interval: 5s
controller:
  listen: 192.0.2.14:9443
  observer_lease: 15s
  local_asn: 65000
  router_id: 192.0.2.14
  next_hop: 192.0.2.14
mup:
  session_rd: "65000:10"
  t1_route_target: "65000:100"
  t2_route_target: "65000:200"
policy:
  - {name: test, dnn: internet, ue_prefix: 10.60.0.0/16, action: direct}
`)
	if err := os.WriteFile(path, data, 0o600); err != nil {
		t.Fatal(err)
	}
	cfg, err := Load(path)
	if err != nil {
		t.Fatal(err)
	}
	if cfg.Observer.FullInterval != 5*time.Second || cfg.Controller.Lease != 15*time.Second {
		t.Fatalf("durations = %v/%v", cfg.Observer.FullInterval, cfg.Controller.Lease)
	}
	if cfg.MUP.TEIDPrefixLength != 32 {
		t.Fatalf("default TEID length = %d", cfg.MUP.TEIDPrefixLength)
	}
}
