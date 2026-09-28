package main

import (
	"context"
	"flag"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/Ytaihei/free5gc-srv6-mup-lab/api/mup/v1/mupv1connect"
	"github.com/Ytaihei/free5gc-srv6-mup-lab/internal/bgp"
	"github.com/Ytaihei/free5gc-srv6-mup-lab/internal/config"
	"github.com/Ytaihei/free5gc-srv6-mup-lab/internal/controller"
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

	speaker := bgp.New(cfg)
	if err := speaker.Start(ctx); err != nil {
		log.Fatalf("start BGP: %v", err)
	}
	defer func() {
		stopCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = speaker.Stop(stopCtx)
	}()

	service := controller.New(cfg, speaker)
	go service.RunLease(ctx)
	mux := http.NewServeMux()
	path, handler := mupv1connect.NewObserverIngestServiceHandler(service)
	mux.Handle(path, handler)
	path, handler = mupv1connect.NewControllerServiceHandler(service)
	mux.Handle(path, handler)
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusNoContent) })
	httpServer := &http.Server{Addr: cfg.Controller.Listen, Handler: mux, ReadHeaderTimeout: 5 * time.Second}
	go func() {
		<-ctx.Done()
		shutdown, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = httpServer.Shutdown(shutdown)
	}()
	log.Printf("MUP controller Connect API listening on %s", cfg.Controller.Listen)
	if err := httpServer.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		log.Fatalf("serve: %v", err)
	}
}
