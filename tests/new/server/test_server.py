"""Acceptance tests for request identity, revisions, task execution and provenance."""

import ast
import subprocess
from unittest.mock import patch

from enterprise_agent.settings import ROOT
from tests.postgres_fixture import (
    identity_scope,
)
from tests.support import AgentTestCase


class ServerTests(AgentTestCase):
    def test_http_requests_revisions_artifacts_and_resume(self):
        from fastapi.testclient import TestClient

        from enterprise_agent.server import app as adapter
        from enterprise_agent.server import auth as identity
        from enterprise_agent.server import deps

        with (
            identity_scope(self.directory.name),
            patch.object(deps, "application", lambda: self.engine),
        ):
            identity.create_user("smoke", "smoke-password-2026", "analyst")
            with TestClient(adapter.app) as client:
                login = client.post(
                    "/auth/login",
                    json={"username": "smoke", "password": "smoke-password-2026"},
                )
                headers = {"Authorization": "Bearer " + login.json()["access_token"]}
                response = client.post(
                    "/ask",
                    json={"question": "解释平均值，再统计产品并生成图表和报告"},
                    headers=headers,
                )
                self.assertEqual(response.status_code, 200, response.text)
                result = response.json()
                self.assertEqual(result["status"], "completed", result["blocks"])
                request = result["requests"][-1]
                self.assertIn("spec", request)
                self.assertNotIn("results", request)
                self.assertIn(request["request_id"], result["deliveries"])
                self.assertEqual(
                    len(
                        client.get(
                            "/requests",
                            params={"session_id": result["session_id"]},
                            headers=headers,
                        ).json()
                    ),
                    2,
                )
                self.assertEqual(
                    client.get(
                        f"/requests/{request['request_id']}", headers=headers
                    ).status_code,
                    200,
                )
                self.assertEqual(
                    len(
                        client.get(
                            f"/requests/{request['request_id']}/revisions",
                            headers=headers,
                        ).json()
                    ),
                    1,
                )
                self.assertEqual(
                    client.get(
                        f"/tasks/{result['tasks'][0]['task_id']}", headers=headers
                    ).status_code,
                    200,
                )
                artifact = self.engine.requests.outputs(request)["data"]["id"]
                self.assertEqual(
                    client.get(f"/artifacts/{artifact}/sql", headers=headers).json()[
                        "provenance"
                    ]["method"],
                    "business_sql",
                )
                self.assertEqual(
                    client.get(
                        f"/artifacts/{artifact}/content?limit=2", headers=headers
                    ).json()["returned_rows"],
                    2,
                )
                orphan = self.engine.responses.message(
                    result["session_id"], result["turn_id"], "请补充统计范围"
                )
                self.assertEqual(
                    client.get(
                        f"/artifacts/{orphan.artifact_id}", headers=headers
                    ).status_code,
                    200,
                )
                self.assertEqual(
                    client.get(
                        f"/artifacts/{orphan.artifact_id}/content", headers=headers
                    ).json()["content"],
                    "请补充统计范围",
                )
                self.assertEqual(
                    client.get(
                        f"/artifacts/{orphan.artifact_id}/download", headers=headers
                    ).status_code,
                    200,
                )
                identity.create_user("other", "other-password-2026", "analyst")
                other = client.post(
                    "/auth/login",
                    json={"username": "other", "password": "other-password-2026"},
                )
                denied = client.get(
                    f"/artifacts/{orphan.artifact_id}",
                    headers={"Authorization": "Bearer " + other.json()["access_token"]},
                )
                self.assertEqual(denied.status_code, 403)
                preview = client.post(
                    "/bind",
                    json={
                        "question": "列出前五个产品",
                        "session_id": result["session_id"],
                    },
                    headers=headers,
                )
                self.assertEqual(
                    preview.json()["status"], "waiting_input", preview.text
                )
                resume_id = preview.json()["requests"][0]["request_id"]
                resumed = client.post(
                    f"/requests/{resume_id}/resume", json={}, headers=headers
                )
                self.assertEqual(resumed.json()["status"], "completed", resumed.text)
                restored = client.post(
                    f"/sessions/{result['session_id']}/turns/{result['turn_id']}/resume",
                    headers=headers,
                )
                self.assertEqual(restored.status_code, 200, restored.text)
                self.assertEqual(
                    restored.json()["response_event_id"], result["response_event_id"]
                )
                self.assertEqual(client.get("/").status_code, 200)
                self.assertEqual(client.get("/chat.css").status_code, 200)

    def test_frontend_renders_only_response_artifacts_in_order(self):
        script = r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
class Element {
  constructor(tag) { this.tag=tag; this.children=[]; this.className=''; this.textContent=''; this.classList={toggle(){},add(){},remove(){}} }
  append(...children) { this.children.push(...children) }
  replaceChildren(...children) { this.children=children }
}
const nodes={conversationLog:new Element('log'),resultEmpty:new Element('empty'),chatScroll:new Element('scroll')};
const calls=[];
const artifacts={
  result_001:{artifact_id:'result_001',content_type:'text',title:'说明',source_artifact_ids:['result_hidden']},
  result_002:{artifact_id:'result_002',content_type:'table',title:'真实数据',summary:'完整数据',payload:{row_count:1}},
  result_003:{artifact_id:'result_003',content_type:'report',title:'报告'},
};
const responses={
  '/sessions/s/events':[{event_type:'assistant_response',payload:{schema_version:2,blocks:[{block_id:'a',artifact_id:'result_001'},{block_id:'b',artifact_id:'result_002'},{block_id:'c',artifact_id:'result_003'}]}}],
  '/artifacts/result_001/content':{content:'平均值的解释'},
  '/artifacts/result_002/content?offset=0&limit=6':{rows:[{n:16}],total_rows:1,has_more:false},
  '/artifacts/result_003/content':{content:'# 报告正文'},
};
const context={document:{createElement:tag=>new Element(tag),getElementById:id=>nodes[id],addEventListener(){}},sessionStorage:{getItem(){return null}},requestAnimationFrame:fn=>fn(),fetch:async path=>{calls.push(path);const data=responses[path]||artifacts[path.split('/').pop()];assert.ok(data, path);return {ok:true,status:200,json:async()=>data}}};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),context);
vm.runInContext("state.sessionId='s'; loadConversation()",context).then(()=>{
  const body=nodes.conversationLog.children[0].children[1];
  assert.equal(body.children.length,3);
  assert.equal(body.children[0].className,'message-text');
  assert.equal(body.children[1].className,'inline-artifact');
  assert.equal(body.children[2].className,'inline-artifact');
  assert.ok(!calls.some(path=>path.includes('result_hidden')));
  assert.ok(body.children[1].children.some(child=>child.tag==='div'&&child.className==='table-wrap'));
}).catch(error=>{console.error(error);process.exitCode=1});
"""
        subprocess.run(
            [
                "node",
                "-e",
                script,
                str(ROOT / "src/enterprise_agent/server/static/app.js"),
            ],
            check=True,
            capture_output=True,
        )

    def test_source_syntax(self):
        for path in (ROOT / "src").rglob("*.py"):
            ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        subprocess.run(
            [
                "node",
                "--check",
                str(ROOT / "src/enterprise_agent/server/static/app.js"),
            ],
            check=True,
            capture_output=True,
        )
