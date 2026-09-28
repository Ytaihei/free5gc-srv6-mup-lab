package dashboard

import (
	"encoding/json"
	"io"
	"os"
	"time"
)

// FileProvider serves an atomic collector snapshot without controller, SSH or
// Docker credentials. A stopped collector must never leave a healthy topology.
type FileProvider struct{ Path string }

func (p FileProvider) Snapshot() State {
	state := State{}
	file, err := os.Open(p.Path)
	if err != nil {
		return unavailableSnapshot(state, "collector snapshot unavailable")
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil || !info.Mode().IsRegular() || info.Size() > 2<<20 {
		return unavailableSnapshot(state, "invalid collector snapshot size/type")
	}
	decoder := json.NewDecoder(io.LimitReader(file, (2<<20)+1))
	if err := decoder.Decode(&state); err != nil {
		return unavailableSnapshot(State{}, "invalid collector snapshot")
	}
	var extra any
	if decoder.Decode(&extra) != io.EOF {
		return unavailableSnapshot(State{}, "invalid trailing collector data")
	}
	age := time.Since(state.UpdatedAt)
	if state.UpdatedAt.IsZero() || age > 30*time.Second || age < -5*time.Second {
		return unavailableSnapshot(state, "collector snapshot is stale")
	}
	return state
}

func unavailableSnapshot(state State, reason string) State {
	state.Stale = true
	state.Overall = "offline"
	state.Controller.Available = false
	state.Controller.ObserverLeaseValid = false
	state.Paths = PathState{}
	state.UPlane.Success = false
	state.UPlane.Status = "idle"
	state.UPlane.Error = reason
	for i := range state.Nodes {
		state.Nodes[i].Healthy = false
		state.Nodes[i].Reachable = false
	}
	for i := range state.Sessions {
		state.Sessions[i].Advertised = false
	}
	state.Errors = append(state.Errors, reason)
	return state
}
