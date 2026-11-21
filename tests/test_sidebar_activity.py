import json
from threading import Event

from codelite.agent.loop import AgentRunner
from codelite.app import runtime as runtime_module
from codelite.app.server import create_app
from codelite.config import AppConfig


def test_sidebar_stream_tracks_background_runs_and_reconnects(tmp_path, monkeypatch):
    app = create_app(AppConfig(data_dir=tmp_path))
    runtime = app.config["runtime"]
    background = runtime.create_conversation(workspace=str(tmp_path), model="gpt-6-astra")
    foreground = runtime.create_conversation(workspace=str(tmp_path), model="gpt-6-astra")
    ask = Event()
    finish = Event()

    def run(runner, _text, _attachments, _message_id):
        if runner._conversation.id == foreground.id:
            runner._publish("error", {"message": "private error text"})
            return
        if ask.wait(2):
            runner._questions.ask("private question text", [])
        finish.wait(2)

    monkeypatch.setattr(AgentRunner, "run", run)
    monkeypatch.setattr(runtime_module, "KEEPALIVE_SECONDS", 1)

    def snapshot(stream):
        frame = next(stream).decode()
        assert frame.startswith("event: activity\ndata: ")
        assert "private" not in frame
        return json.loads(frame.split("data: ", 1)[1])["conversations"]

    with app.test_client().get("/api/conversation-activity", buffered=False) as response:
        assert response.mimetype == "text/event-stream"
        stream = iter(response.response)
        assert snapshot(stream) == {}
        runtime.start_run(background, "Start")
        running = snapshot(stream)[background.id]
        assert running["status"] == "running"

        ask.set()
        assert snapshot(stream)[background.id]["status"] == "waiting"
        question, = runtime._state_for(background).questions.list_pending()
        assert runtime.reply_question(background, question["id"], "Continue")
        assert snapshot(stream)[background.id]["status"] == "running"

        finish.set()
        assert snapshot(stream)[background.id] == {**running, "status": "finished"}
        runtime.start_run(foreground, "Fail")
        runtime._state_for(foreground).thread.join(timeout=2)
        result = snapshot(stream)
        assert result[foreground.id]["status"] == "failed"

    with app.test_client().get("/api/conversation-activity", buffered=False) as response:
        assert snapshot(iter(response.response)) == result
