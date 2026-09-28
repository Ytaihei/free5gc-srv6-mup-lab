package dashboard

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

type fixedProvider struct{ state State }

func (p fixedProvider) Snapshot() State { return p.state }

func TestStateAPIAndHeaders(t *testing.T) {
	state := State{UpdatedAt: time.Now(), Overall: "healthy"}
	request := httptest.NewRequest(http.MethodGet, "/api/state", nil)
	recorder := httptest.NewRecorder()
	Handler(fixedProvider{state: state}).ServeHTTP(recorder, request)
	if recorder.Code != http.StatusOK {
		t.Fatalf("status = %d", recorder.Code)
	}
	if !strings.Contains(recorder.Header().Get("Content-Security-Policy"), "default-src 'self'") {
		t.Fatal("missing content security policy")
	}
	var got State
	if err := json.NewDecoder(recorder.Body).Decode(&got); err != nil {
		t.Fatal(err)
	}
	if got.Overall != "healthy" {
		t.Fatalf("overall = %q", got.Overall)
	}
}

func TestHealthRejectsStaleState(t *testing.T) {
	request := httptest.NewRequest(http.MethodGet, "/healthz", nil)
	recorder := httptest.NewRecorder()
	Handler(fixedProvider{}).ServeHTTP(recorder, request)
	if recorder.Code != http.StatusServiceUnavailable {
		t.Fatalf("status = %d", recorder.Code)
	}
}

func TestDashboardUsesMUPPENamesAndStableIDs(t *testing.T) {
	for _, path := range []string{"/", "/app.js"} {
		recorder := httptest.NewRecorder()
		Handler(fixedProvider{}).ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, path, nil))
		if recorder.Code != http.StatusOK {
			t.Fatalf("%s: status = %d", path, recorder.Code)
		}
		body := recorder.Body.String()
		for _, label := range []string{"MUP PE（N3／Interwork側）", "MUP PE（N6／Direct側）"} {
			if !strings.Contains(body, label) {
				t.Fatalf("%s is missing %s", path, label)
			}
		}
		for _, old := range []string{"T-PE", "N-PE", "MUP-GW", "MUP-PE"} {
			if strings.Contains(body, old) {
				t.Fatalf("%s still uses legacy display name %s", path, old)
			}
		}
		if path == "/" {
			for _, id := range []string{"tpe", "npe"} {
				if !strings.Contains(body, `data-node="`+id+`"`) || !strings.Contains(body, "ID: "+id) {
					t.Fatalf("missing stable topology ID %s", id)
				}
			}
		}
	}
}

func TestFileProviderFreshnessAndFailure(t *testing.T) {
	path := filepath.Join(t.TempDir(), "state.json")
	provider := FileProvider{Path: path}
	if !provider.Snapshot().Stale {
		t.Fatal("missing file accepted")
	}
	for _, age := range []time.Duration{0, 31 * time.Second, -6 * time.Second} {
		state := State{UpdatedAt: time.Now().Add(-age), Overall: "healthy",
			Controller: ControllerStatus{Available: true, ObserverLeaseValid: true},
			Paths:      PathState{MUPActive: true}, UPlane: UPlaneProbe{Success: true, Status: "ok"},
			Nodes: []Node{{Healthy: true, Reachable: true}}, Sessions: []Session{{Advertised: true}}}
		data, _ := json.Marshal(state)
		if err := os.WriteFile(path, data, 0600); err != nil {
			t.Fatal(err)
		}
		got := provider.Snapshot()
		if age == 0 {
			if got.Stale || !got.UPlane.Success {
				t.Fatal("fresh state rejected")
			}
		} else if !got.Stale || got.Controller.Available || got.Controller.ObserverLeaseValid ||
			got.Paths.MUPActive || got.Paths.FallbackActive || got.UPlane.Success ||
			got.Nodes[0].Healthy || got.Nodes[0].Reachable || got.Sessions[0].Advertised {
			t.Fatalf("old/future state still appears healthy: %+v", got)
		}
	}
	for _, data := range []string{"{", "null", "{} {}", strings.Repeat(" ", (2<<20)+1)} {
		if err := os.WriteFile(path, []byte(data), 0600); err != nil {
			t.Fatal(err)
		}
		recorder := httptest.NewRecorder()
		Handler(provider).ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, "/healthz", nil))
		if recorder.Code != http.StatusServiceUnavailable {
			t.Fatal("invalid file passed health")
		}
	}
}
