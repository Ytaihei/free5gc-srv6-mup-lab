package dashboard

import (
	"context"
	"sync"
	"time"
)

type StateCollector interface {
	Collect(context.Context) State
}

type Monitor struct {
	collector StateCollector
	mu        sync.RWMutex
	state     State
}

func NewMonitor(collector StateCollector) *Monitor {
	return &Monitor{collector: collector, state: State{Overall: "starting"}}
}

func (m *Monitor) Run(ctx context.Context, interval time.Duration) {
	m.update(ctx)
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			m.update(ctx)
		}
	}
}

func (m *Monitor) update(parent context.Context) {
	ctx, cancel := context.WithTimeout(parent, 8*time.Second)
	defer cancel()
	state := m.collector.Collect(ctx)
	m.mu.Lock()
	m.state = state
	m.mu.Unlock()
}

func (m *Monitor) Snapshot() State {
	m.mu.RLock()
	defer m.mu.RUnlock()
	return m.state
}
