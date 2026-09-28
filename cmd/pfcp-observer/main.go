package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/signal"
	"sync"
	"syscall"
	"time"

	"connectrpc.com/connect"
	"github.com/google/gopacket"
	"github.com/google/gopacket/layers"
	"github.com/google/gopacket/pcapgo"
	mupv1 "github.com/Ytaihei/free5gc-srv6-mup-lab/api/mup/v1"
	"github.com/Ytaihei/free5gc-srv6-mup-lab/api/mup/v1/mupv1connect"
	"github.com/Ytaihei/free5gc-srv6-mup-lab/internal/config"
	"github.com/Ytaihei/free5gc-srv6-mup-lab/internal/pfcpstate"
)

func main() {
	configPath := flag.String("config", "/etc/srv6-mup/config.yml", "configuration file")
	flag.Parse()
	cfg, err := config.Load(*configPath)
	if err != nil {
		log.Fatalf("load config: %v", err)
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	capture, err := pcapgo.NewEthernetHandle(cfg.Observer.Interface)
	if err != nil {
		log.Fatalf("open AF_PACKET on %s: %v", cfg.Observer.Interface, err)
	}
	defer capture.Close()
	if err := capture.SetCaptureLength(65536); err != nil {
		log.Fatalf("set capture length on %s: %v", cfg.Observer.Interface, err)
	}
	// PFCP crosses a Linux bridge between the SMF and UPF containers.  A packet
	// socket bound to the bridge only sees that forwarded traffic when it joins
	// the interface's promiscuous membership.  This is socket-local and is
	// automatically removed when capture is closed.
	if err := capture.SetPromiscuous(true); err != nil {
		log.Fatalf("enable promiscuous capture on %s: %v", cfg.Observer.Interface, err)
	}

	state := pfcpstate.New()
	publisher := newPublisher(cfg, state)
	go publisher.run(ctx)

	source := gopacket.NewPacketSource(capture, layers.LinkTypeEthernet)
	source.DecodeOptions.Lazy = true
	source.DecodeOptions.NoCopy = true
	log.Printf("passively observing PFCP UDP/8805 on %s", cfg.Observer.Interface)
	for {
		select {
		case <-ctx.Done():
			return
		case packet, ok := <-source.Packets():
			if !ok {
				return
			}
			ipLayer := packet.Layer(layers.LayerTypeIPv4)
			udpLayer := packet.Layer(layers.LayerTypeUDP)
			if ipLayer == nil || udpLayer == nil {
				continue
			}
			ip := ipLayer.(*layers.IPv4)
			udp := udpLayer.(*layers.UDP)
			if udp.SrcPort != 8805 && udp.DstPort != 8805 {
				continue
			}
			src := fmt.Sprintf("%s:%d", ip.SrcIP, udp.SrcPort)
			dst := fmt.Sprintf("%s:%d", ip.DstIP, udp.DstPort)
			changed, err := state.Consume(src, dst, udp.Payload, time.Now())
			if err != nil {
				log.Printf("discard malformed/incomplete PFCP packet: %v", err)
				continue
			}
			if changed {
				publisher.trigger()
			}
		}
	}
}

type publisher struct {
	cfg        config.Config
	state      *pfcpstate.State
	client     mupv1connect.ObserverIngestServiceClient
	triggerC   chan struct{}
	mu         sync.Mutex
	generation uint64
}

func newPublisher(cfg config.Config, state *pfcpstate.State) *publisher {
	httpClient := &http.Client{Timeout: 4 * time.Second}
	return &publisher{
		cfg: cfg, state: state,
		client:     mupv1connect.NewObserverIngestServiceClient(httpClient, cfg.Observer.ControllerURL),
		triggerC:   make(chan struct{}, 1),
		generation: uint64(time.Now().UnixNano()),
	}
}

func (p *publisher) trigger() {
	select {
	case p.triggerC <- struct{}{}:
	default:
	}
}

func (p *publisher) run(ctx context.Context) {
	ticker := time.NewTicker(p.cfg.Observer.FullInterval)
	defer ticker.Stop()
	p.publish(ctx)
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			p.publish(ctx)
		case <-p.triggerC:
			p.publish(ctx)
		}
	}
}

func (p *publisher) publish(parent context.Context) {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.generation++
	now := time.Now()
	sessions := p.state.Snapshot()
	protoSessions := make([]*mupv1.Session, 0, len(sessions))
	for _, session := range sessions {
		protoSessions = append(protoSessions, session.Proto())
	}
	ctx, cancel := context.WithTimeout(parent, 4*time.Second)
	defer cancel()
	_, err := p.client.SyncSnapshot(ctx, connect.NewRequest(&mupv1.SyncSnapshotRequest{
		ObserverId: p.cfg.Observer.ID, Generation: p.generation,
		ObservedUnixNano: now.UnixNano(), Sessions: protoSessions,
	}))
	if err != nil {
		log.Printf("snapshot generation %d delivery failed: %v", p.generation, err)
	}
}
