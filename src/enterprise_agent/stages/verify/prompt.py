"""Model instructions owned by this stage."""

SUPERVISOR_COMPLETION = (
    "逐项检查当前 request.spec.outputs，返回 outputs 列表、next_action 和 reason。"
    "每个 name 必须覆盖一次，completed 必须引用实际交付绑定所解析的 Artifact。"
    "检查实际内容和类型，不能只看最后一个节点完成。缺口用 unfulfilled/needs_clarification。"
    "还能通过当前目标内的工作补足时 next_action=replan；可回答时 answer；需要用户确认时 clarify。"
    "不能把 Request 引用自身当作证据，不能验收其他历史 Request。"
)
