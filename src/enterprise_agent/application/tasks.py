"""Revision merging, plan validation and logical output resolution."""
from ..core.models import Requirement, Plan
from ..core.config import MAX_SUBTASKS
from ..persistence.stores import now


class Tasks:
    def __init__(self, stores):
        self.stores = stores
    
    # 1. 给add操作的req id 2. inputs_biding的request类型的id换为刚刚确定好的 3.ref的id也换为刚刚确定好的 
    def canonicalize(self, intent):
        aliases = {change.request_id: self.stores.new_id("req") for change in intent.request_changes if change.action == "add"}
        for change in intent.request_changes:
            change.request_id = aliases.get(change.request_id, change.request_id)
            for binding in change.inputs:
                if binding.source.kind == "request":
                    binding.source.id = aliases.get(binding.source.id, binding.source.id)
        for ref in intent.refs:
            if ref.kind == "request":
                ref.id = aliases.get(ref.id, ref.id)

    def revise(self, session_id, turn_id, intent, event_id):
        continuing = intent.dialogue_action in ("continue", "modify")
        if continuing:
            if not intent.target_task_id:
                raise ValueError("继续任务必须明确目标 Task")
            task = self.stores.get("tasks", intent.target_task_id)
            if task["origin_session_id"] != session_id:
                raise ValueError("目标任务不属于当前会话")
            previous = self.stores.get("task_revisions", task["current_revision_id"])
            requirements = {r["request_id"]: r for r in previous["requirements"]}
            constraints = dict(previous["constraints"])
        else:
            task = {"task_id": self.stores.new_id("task"),
                    "origin_session_id": session_id, "created_by_turn_id": turn_id,
                    "goal": intent.goal, "current_revision_id": None, "plan_version": 0,
                    "status": "active", "created_at": now(), "updated_at": now()}
            previous, requirements, constraints = None, {}, {}
        aliases = {}
        changed = set()
        for change in intent.request_changes:
            key = aliases.get(change.request_id, change.request_id)
            if change.action == "add" and key in requirements:
                raise ValueError("重复需求 ID")
            if change.action in ("replace", "remove") and key not in requirements:
                raise ValueError("修改的需求不存在")
            changed.add(key) # add和replace加进去了
            if change.action == "remove":
                del requirements[key]
            else: # add和replace
                record = change.model_dump(exclude={"action"}, exclude_none=True)
                record["request_id"] = key
                for binding in record["inputs"]:
                    source = binding["source"]
                    if source["kind"] == "request":
                        source["id"] = aliases.get(source["id"], source["id"])
                requirements[key] = Requirement.model_validate(record).model_dump(exclude_none=True)
        for key, value in intent.constraints_patch.items():
            if value is None:
                constraints.pop(key, None)
            else:
                constraints[key] = value
        for requirement in requirements.values():
            for binding in requirement["inputs"]:
                ref = binding["source"]
                self.validate_ref(session_id, ref, requirements=requirements)
        # Requirement dependency graph must itself be acyclic.
        visiting, done = set(), set()
        def visit(key):
            if key in visiting:
                raise ValueError("需求引用存在循环")
            if key in done:
                return
            visiting.add(key)
            for binding in requirements[key]["inputs"]:
                if binding["source"]["kind"] == "request":
                    visit(binding["source"]["id"])
            visiting.remove(key)
            done.add(key)
        for key in requirements:
            visit(key)
        revision = {"revision_id": self.stores.new_id("rev"), "task_id": task["task_id"],
                    "parent_revision_id": task["current_revision_id"], "source_intent_event_id": event_id,
                    "constraints": constraints, "requirements": list(requirements.values()), "created_at": now()}
        if not requirements:
            raise ValueError("业务任务至少需要一项完整需求")
        if previous:
            nodes = self.stores.list("subtasks", "task_id", task["task_id"])
            affected = {node["subtask_id"] for node in nodes if set(node["request_ids"]) & changed # request变，引起原先对应的subtask改变（不包括modify里面新增的，因为其id add的时候自动更新了）
                        or previous["constraints"] != constraints} # &交集，其subtask对应的request_ids 与之前对应的changed里面的request_ids
            while True:
                expanded = affected | {node["subtask_id"] for node in nodes if set(node["depends_on"]) & affected} # 闭包，受影响的subtask的闭包都受影响
                if expanded == affected:
                    break
                affected = expanded 
            for node in nodes:
                if node["subtask_id"] in affected:
                    node["status"] = "superseded"
                    self.stores.put("subtasks", node) # 原本冲突的subtask更新，注意这边是没有modify和continue的add被标记的
        task.update(current_revision_id=revision["revision_id"], goal=intent.goal or task["goal"],
                    status="active", updated_at=now()) # task更新，continue，modify和new_task一样
        self.stores.put("tasks", task)  # 更新的task就body替换，没更新的直接插入
        self.stores.put("task_revisions", revision) # task_revisions一样，new_task就是全新的，然后continue和modify就是拿原来的来更新
        self.stores.activate(session_id, task["task_id"])
        return task, revision

    def validate_ref(self, session_id, ref, requirements=None):
        kind, key = ref["kind"], ref["id"]
        if kind == "request":
            if requirements is not None and key in requirements:
                return
            raise ValueError("需求引用不存在")
        table = {"subtask": "subtasks", "artifact": "artifacts", "event": "session_events"}[kind]
        item = self.stores.get(table, key)
        origin = item.get("session_id")
        if origin is None:
            origin = self.stores.get("tasks", item["task_id"])["origin_session_id"]
        if origin != session_id:
            raise ValueError("引用不属于当前会话")

    def apply_plan(self, task, turn_id, plan: Plan):
        nodes = self.stores.list("subtasks", "task_id", task["task_id"]) # 根据task来
        existing = {node["subtask_id"]: node for node in nodes} # 全部subtask节点，带有状态superseded等状态，记为上个turn_subtask
        revision = self.stores.get("task_revisions", task["current_revision_id"])
        requests = {item["request_id"] for item in revision["requirements"]}
        cancelled = set(plan.cancel_subtask_ids) 
        if any(key not in existing or existing[key]["status"] != "pending" for key in cancelled):
            raise ValueError("仅可撤销尚未执行的节点")
        local_ids = [item.subtask_id for item in plan.subtasks] # 待执行节点
        if len(local_ids) != len(set(local_ids)) or set(local_ids) & existing.keys():
            raise ValueError("新节点 ID 必须唯一")
        if len([node for node in nodes if node["status"] not in ("cancelled", "superseded")]) + len(local_ids) - len(cancelled) > MAX_SUBTASKS:
            raise ValueError("任务超过节点预算")
        aliases = {key: self.stores.new_id("subtask") for key in local_ids} # 只有计划的subtask
        additions = []
        for proposed in plan.subtasks:
            node = proposed.model_dump(exclude_none=True)
            if not node["request_ids"] or not set(node["request_ids"]) <= requests:
                raise ValueError("节点必须关联当前需求")
            if len(node["expected_outputs"]) != len(set(node["expected_outputs"])):
                raise ValueError("输出名称重复")
            if node["assigned_agent"] == "SemanticAgent" and node["expected_outputs"]:
                raise ValueError("Semantic 绑定通过轻量执行结果传递，不声明 Artifact 输出")
            node.update(subtask_id=aliases[proposed.subtask_id], task_id=task["task_id"],
                revision_id=task["current_revision_id"], created_by_turn_id=turn_id,
                status="pending", produced_outputs={}, created_at=now()) # 这个是把(计划出来的subtask)更新了
            node["depends_on"] = [aliases.get(key, key) for key in node["depends_on"]] # id换算
            for binding in node["input_bindings"]:
                ref = binding["source"]
                if ref["kind"] == "subtask":
                    ref["id"] = aliases.get(ref["id"], ref["id"])
            additions.append(node) # 这个地方完成了新subtask的创建和格式修改
        combined = {**existing, **{node["subtask_id"]: node for node in additions}} # 所有的subtask合并，原本有的和新创建出来的合并
        for node in additions + [x for x in nodes if x["status"] == "pending" and x["subtask_id"] not in cancelled]:
            for dep in node["depends_on"]:
                if dep not in combined or dep in cancelled or combined[dep]["status"] in ("superseded", "cancelled"):
                    raise ValueError("节点依赖无效")
            for binding in node["input_bindings"]:
                ref = binding["source"]
                if ref["kind"] == "subtask":
                    source = combined.get(ref["id"])
                    if not source or ref["id"] not in node["depends_on"]:
                        raise ValueError("上游输出绑定必须有显式依赖")
                    output = ref.get("output")
                    semantic_output = source["assigned_agent"] == "SemanticAgent" and output == "semantic_binding"
                    if output and output not in source["expected_outputs"] and not semantic_output:
                        raise ValueError("上游未声明该命名输出")
                else:
                    self.validate_ref(task["origin_session_id"], ref, {key: True for key in requests})
        visiting, seen = set(), set()
        def visit(key):
            if key in visiting:
                raise ValueError("Subtask DAG 存在环")
            if key in seen:
                return
            visiting.add(key)
            for dep in combined[key]["depends_on"]:
                visit(dep)
            visiting.remove(key)
            seen.add(key)
        for node in additions:
            visit(node["subtask_id"])
        for key in cancelled:
            existing[key]["status"] = "cancelled"
            self.stores.put("subtasks", existing[key])
        for node in additions:
            self.stores.put("subtasks", node)
        task["plan_version"] += 1
        task["updated_at"] = now()
        self.stores.put("tasks", task)

    def resolve(self, task, binding):
        ref = binding["source"]
        self.validate_ref(task["origin_session_id"], ref,
            {r["request_id"]: r for r in self.stores.get("task_revisions", task["current_revision_id"])["requirements"]})
        if ref["kind"] == "artifact":
            return {"slot": binding["slot"], "artifact_id": ref["id"]}
        if ref["kind"] == "event":
            return {"slot": binding["slot"], "event": self.stores.get("session_events", ref["id"])}
        nodes = self.stores.list("subtasks", "task_id", task["task_id"])
        if ref["kind"] == "request":
            candidates = [n for n in nodes if ref["id"] in n["request_ids"] and n["status"] == "completed"]
            outputs = [value for n in candidates for value in n["produced_outputs"].values()]
            revision = self.stores.get("task_revisions", task["current_revision_id"])
            requirement = next(item for item in revision["requirements"] if item["request_id"] == ref["id"])
            # A Request refers to the requirement itself, even before it has results.
            # Actual data dependencies still use named Subtask outputs or Artifacts.
            return {"slot": binding["slot"], "requirement": requirement,
                    "artifact_ids": list(dict.fromkeys(r["id"] for r in outputs if r["kind"] == "artifact"))}
        node = self.stores.get("subtasks", ref["id"])
        if node["status"] != "completed":
            raise ValueError("上游节点尚未完成")
        if node.get("semantic_binding"):
            return {"slot": binding["slot"], "semantic_binding": node["semantic_binding"]}
        outputs = node["produced_outputs"]
        key = ref.get("output")
        if key is None:
            if len(outputs) != 1:
                raise ValueError("多个上游输出必须指定 output")
            key = next(iter(outputs))
        return {"slot": binding["slot"], "artifact_id": outputs[key]["id"]}
