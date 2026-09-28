package policy

import (
	"testing"

	"github.com/Ytaihei/free5gc-srv6-mup-lab/internal/config"
	"github.com/Ytaihei/free5gc-srv6-mup-lab/internal/model"
)

func TestEvaluateFirstMatchAndDefaultUPF(t *testing.T) {
	rules := []config.PolicyRule{
		{Name: "internet-lab", DNN: "internet", UEPrefix: "10.60.0.0/16", Action: "direct"},
		{Name: "explicit-fallback", DNN: "ims", Action: "upf"},
	}
	if got := Evaluate(rules, model.Session{DNN: "internet", UEIPv4: "10.60.0.7"}); !got.Selected || got.Reason != "policy:internet-lab:direct" {
		t.Fatalf("direct decision = %+v", got)
	}
	if got := Evaluate(rules, model.Session{DNN: "internet", UEIPv4: "10.61.0.7"}); got.Selected || got.Reason != "default:upf" {
		t.Fatalf("out-of-pool decision = %+v", got)
	}
	if got := Evaluate(rules, model.Session{DNN: "ims", UEIPv4: "10.60.0.8"}); got.Selected || got.Reason != "policy:explicit-fallback:upf" {
		t.Fatalf("explicit fallback decision = %+v", got)
	}
}
