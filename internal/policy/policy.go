package policy

import (
	"net/netip"
	"strings"

	"github.com/Ytaihei/free5gc-srv6-mup-lab/internal/config"
	"github.com/Ytaihei/free5gc-srv6-mup-lab/internal/model"
)

type Decision struct {
	Selected bool
	Reason   string
}

func Evaluate(rules []config.PolicyRule, s model.Session) Decision {
	ue, _ := netip.ParseAddr(s.UEIPv4)
	for _, r := range rules {
		if r.DNN != "" && !strings.EqualFold(r.DNN, s.DNN) {
			continue
		}
		if r.SUPIPrefix != "" && !strings.HasPrefix(s.SUPI, r.SUPIPrefix) {
			continue
		}
		if r.UEPrefix != "" {
			p, err := netip.ParsePrefix(r.UEPrefix)
			if err != nil || !p.Contains(ue) {
				continue
			}
		}
		return Decision{Selected: r.Action == "direct", Reason: "policy:" + r.Name + ":" + r.Action}
	}
	return Decision{Reason: "default:upf"}
}
