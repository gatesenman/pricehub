"""v5 价格全生命周期接口层：只做路由注册与事件接线，业务实现见 app/domains/*（v7 下沉）。"""
import os
from fastapi.responses import HTMLResponse
from app.main import app, HOOKS
import app.domains.common as svc_common
import app.domains.normalize.service as svc_normalize
import app.domains.alerting.service as svc_alerting
import app.domains.match.service as svc_match
import app.domains.analysis.advice as svc_analysis
import app.domains.pricing.service as svc_pricing
import app.domains.integration.service as svc_integration
import app.domains.collect.settings_service as svc_collect
import app.domains.admin.service as svc_admin

# ---- 路由表（顺序与 v5 保持一致） ----
app.add_api_route("/api/fx", svc_normalize.get_fx, methods=["GET"])
app.add_api_route("/api/fx", svc_normalize.set_fx, methods=["POST"])
app.add_api_route("/api/products/{pid}/costs", svc_normalize.set_costs, methods=["PUT"])
app.add_api_route("/api/products/{pid}/full", svc_normalize.product_full, methods=["GET"])
app.add_api_route("/api/compare/landed", svc_normalize.compare_landed, methods=["GET"])
app.add_api_route("/api/channels", svc_alerting.channels, methods=["GET"])
app.add_api_route("/api/channels", svc_alerting.add_channel, methods=["POST"])
app.add_api_route("/api/channels/{cid}", svc_alerting.del_channel, methods=["DELETE"])
app.add_api_route("/api/channels/{cid}/test", svc_alerting.test_channel, methods=["POST"])
app.add_api_route("/api/notifications", svc_alerting.notifications, methods=["GET"])
app.add_api_route("/api/rules", svc_alerting.rules, methods=["GET"])
app.add_api_route("/api/rules", svc_alerting.add_rule, methods=["POST"])
app.add_api_route("/api/rules/{rid}", svc_alerting.edit_rule, methods=["PUT"])
app.add_api_route("/api/rules/{rid}", svc_alerting.del_rule, methods=["DELETE"])
app.add_api_route("/api/match/suggest", svc_match.match_suggest, methods=["GET"])
app.add_api_route("/api/match/merge", svc_match.match_merge, methods=["POST"])
app.add_api_route("/api/promo/calendar", svc_analysis.promo_calendar, methods=["GET"])
app.add_api_route("/api/products/{pid}/advice", svc_analysis.buy_advice, methods=["GET"])
app.add_api_route("/api/map/prices", svc_pricing.map_prices, methods=["GET"])
app.add_api_route("/api/map/prices", svc_pricing.set_map, methods=["POST"])
app.add_api_route("/api/map/violations", svc_pricing.map_violations, methods=["GET"])
app.add_api_route("/api/map/evidence/{pid}", svc_pricing.evidence_task, methods=["POST"])
app.add_api_route("/api/map/evidence", svc_pricing.evidence_list, methods=["GET"])
app.add_api_route("/api/map/evidence/{eid}/image", svc_pricing.evidence_image, methods=["GET"])
app.add_api_route("/api/sellers", svc_pricing.sellers, methods=["GET"])
app.add_api_route("/api/pricing/rules", svc_pricing.pricing_rules, methods=["GET"])
app.add_api_route("/api/pricing/rules", svc_pricing.add_pricing_rule, methods=["POST"])
app.add_api_route("/api/pricing/rules/{rid}", svc_pricing.del_pricing_rule, methods=["DELETE"])
app.add_api_route("/api/pricing/simulate", svc_pricing.simulate, methods=["POST"])
app.add_api_route("/api/pricing/push", svc_pricing.pricing_push, methods=["POST"])
app.add_api_route("/api/reports/summary", svc_integration.report, methods=["GET"])
app.add_api_route("/api/reports/send", svc_integration.report_send, methods=["POST"])
app.add_api_route("/api/reports/schedule", svc_integration.get_schedule, methods=["GET"])
app.add_api_route("/api/reports/schedule", svc_integration.set_schedule, methods=["POST"])
app.add_api_route("/api/collect/settings", svc_collect.collect_settings, methods=["GET"])
app.add_api_route("/api/collect/settings", svc_collect.set_collect, methods=["POST"])
app.add_api_route("/api/render/test", svc_collect.render_test, methods=["POST"])
app.add_api_route("/api/tokens", svc_admin.tokens, methods=["GET"])
app.add_api_route("/api/tokens", svc_admin.add_token, methods=["POST"])
app.add_api_route("/api/tokens/{tid}", svc_admin.del_token, methods=["DELETE"])
app.add_api_route("/api/tokens/auth", svc_admin.set_auth, methods=["POST"])
app.add_api_route("/api/audit", svc_admin.audit, methods=["GET"])
app.middleware("http")(svc_admin.auth_audit)
app.add_api_route("/api/backup", svc_admin.backup, methods=["GET"])
app.add_api_route("/api/restore", svc_admin.restore, methods=["POST"])
app.add_api_route("/bookmarklet", svc_integration.bookmarklet, methods=["GET"], response_class=HTMLResponse)

# ---- 事件与定时任务接线 ----
HOOKS.append(svc_alerting.on_event)
svc_integration._sched_report()
os.makedirs(svc_common.EVID_DIR, exist_ok=True)
# 兼容旧代码：from app.v5 import X 仍可用
EVID_DIR = svc_common.EVID_DIR
_latest = svc_common._latest
_now = svc_common._now
EDITABLE = svc_normalize.EDITABLE
FX_DEFAULT = svc_normalize.FX_DEFAULT
compare_landed = svc_normalize.compare_landed
fx = svc_normalize.fx
get_fx = svc_normalize.get_fx
landed = svc_normalize.landed
product_full = svc_normalize.product_full
set_costs = svc_normalize.set_costs
set_fx = svc_normalize.set_fx
to_cny = svc_normalize.to_cny
CHANNEL_TYPES = svc_alerting.CHANNEL_TYPES
RULE_TYPES = svc_alerting.RULE_TYPES
_check = svc_alerting._check
_map_of = svc_alerting._map_of
_payload = svc_alerting._payload
add_channel = svc_alerting.add_channel
add_rule = svc_alerting.add_rule
channels = svc_alerting.channels
del_channel = svc_alerting.del_channel
del_rule = svc_alerting.del_rule
edit_rule = svc_alerting.edit_rule
notifications = svc_alerting.notifications
on_event = svc_alerting.on_event
rules = svc_alerting.rules
send = svc_alerting.send
test_channel = svc_alerting.test_channel
MODEL_RE = svc_match.MODEL_RE
_model = svc_match._model
match_merge = svc_match.match_merge
match_suggest = svc_match.match_suggest
_nth_weekday = svc_analysis._nth_weekday
buy_advice = svc_analysis.buy_advice
promo_calendar = svc_analysis.promo_calendar
promo_events = svc_analysis.promo_events
seasonality = svc_analysis.seasonality
STRATEGIES = svc_pricing.STRATEGIES
add_pricing_rule = svc_pricing.add_pricing_rule
apply_rule = svc_pricing.apply_rule
capture_evidence = svc_pricing.capture_evidence
del_pricing_rule = svc_pricing.del_pricing_rule
evidence_image = svc_pricing.evidence_image
evidence_list = svc_pricing.evidence_list
evidence_task = svc_pricing.evidence_task
map_prices = svc_pricing.map_prices
map_violations = svc_pricing.map_violations
pricing_push = svc_pricing.pricing_push
pricing_rules = svc_pricing.pricing_rules
sellers = svc_pricing.sellers
set_map = svc_pricing.set_map
simulate = svc_pricing.simulate
_sched_report = svc_integration._sched_report
bookmarklet = svc_integration.bookmarklet
get_schedule = svc_integration.get_schedule
report = svc_integration.report
report_data = svc_integration.report_data
report_send = svc_integration.report_send
report_text = svc_integration.report_text
send_report = svc_integration.send_report
set_schedule = svc_integration.set_schedule
collect_settings = svc_collect.collect_settings
render_test = svc_collect.render_test
set_collect = svc_collect.set_collect
ADMIN_PATHS = svc_admin.ADMIN_PATHS
LOOPBACK = svc_admin.LOOPBACK
ROLES = svc_admin.ROLES
_hash = svc_admin._hash
add_token = svc_admin.add_token
audit = svc_admin.audit
auth_audit = svc_admin.auth_audit
backup = svc_admin.backup
del_token = svc_admin.del_token
restore = svc_admin.restore
set_auth = svc_admin.set_auth
tokens = svc_admin.tokens
