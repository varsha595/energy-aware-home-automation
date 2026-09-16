"""
FastAPI backend for the dashboard. Owns one live Engine (backend/executor.py)
in memory, runs it forward in a background thread, and exposes a small REST
API for the frontend to poll state from and to fire the two replanning
triggers from ("Trigger Fault" / "Add New Goal" buttons in the UI).

This process is intentionally single-session (one house, one demo run at a
time) -- that matches "one planner coordinating multiple agents" from
docs/DESIGN.md section 5; it isn't meant to serve multiple concurrent homes.
"""

import threading
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.domain import ROOMS, ADJACENCY, BUDGET_PER_WINDOW, price_multiplier
from backend.executor import Engine, Scenario

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

app = FastAPI(title="Energy-Aware Home Automation Dashboard")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def default_scenario() -> Scenario:
    return Scenario(
        dirty_rooms=["Kitchen", "Bedroom"],
        robot_start="LivingRoom",
        loads=["Load1"],
        dishes_dirty=True,
        start_minute=9 * 60,  # 09:00, inside the peak window -- makes the
                               # energy-aware planner's off-peak deferral visible immediately
    )


class SimRunner:
    """Wraps an Engine with a background thread that keeps calling
    engine.step() while `running` is set, at an adjustable pace."""

    def __init__(self):
        self.lock = threading.Lock()
        self.engine: Engine = Engine(default_scenario())
        self.running = True
        self.delay = 0.6  # real seconds between steps
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        while True:
            time.sleep(self.delay)
            with self.lock:
                if self.running and not self.engine.done and not self.engine.failed:
                    self.engine.step()

    def reset(self, scenario: Scenario):
        with self.lock:
            self.engine = Engine(scenario)
            self.running = True

    def snapshot(self, since_seq: int = 0) -> dict:
        with self.lock:
            eng = self.engine
            rooms = []
            for r in ROOMS:
                rooms.append({
                    "name": r,
                    "dirty": ("dirty", r) in eng.predicates,
                    "clean": ("clean", r) in eng.predicates,
                    "robot_here": ("robot-at", r) in eng.predicates,
                })

            loads = []
            for load in eng.loads:
                loads.append({
                    "name": load,
                    "ready": ("laundry-ready", load) in eng.predicates,
                    "loaded": ("laundry-loaded", load) in eng.predicates,
                    "detergent_added": ("detergent-added", load) in eng.predicates,
                    "wash_done": ("wash-done", load) in eng.predicates,
                    "dry_done": ("dry-done", load) in eng.predicates,
                })

            dishwasher = {
                "dirty": ("dishes-dirty",) in eng.predicates,
                "loaded": ("dishes-loaded",) in eng.predicates,
                "detergent_added": ("dw-detergent-added",) in eng.predicates,
                "clean": ("dishes-clean",) in eng.predicates,
            }

            upcoming = eng.plan_steps[eng.plan_cursor:eng.plan_cursor + 10]
            done_steps = eng.plan_steps[max(0, eng.plan_cursor - 5):eng.plan_cursor]

            events = [
                {"seq": e.seq, "sim_minute": e.sim_minute, "kind": e.kind, "message": e.message}
                for e in eng.events if e.seq > since_seq
            ]

            return {
                "sim_minute": eng.sim_minute,
                "clock": _format_clock(eng.sim_minute),
                "budget_remaining": round(eng.budget_remaining, 1),
                "budget_per_window": BUDGET_PER_WINDOW,
                "price_multiplier": price_multiplier(eng.sim_minute),
                "is_peak": price_multiplier(eng.sim_minute) > 1.0,
                "rooms": rooms,
                "adjacency": ADJACENCY,
                "loads": loads,
                "dishwasher": dishwasher,
                "plan_done": done_steps,
                "plan_upcoming": upcoming,
                "plan_cursor": eng.plan_cursor,
                "plan_length": len(eng.plan_steps),
                "total_energy_used": round(eng.total_energy_used, 1),
                "replan_count": eng.replan_count,
                "done": eng.done,
                "failed": eng.failed,
                "running": self.running,
                "events": events,
                "latest_seq": eng.events[-1].seq if eng.events else 0,
            }


def _format_clock(sim_minute: int) -> str:
    tod = sim_minute % (24 * 60)
    day = sim_minute // (24 * 60)
    h, m = divmod(tod, 60)
    prefix = f"Day {day + 1}, " if day > 0 else ""
    return f"{prefix}{h:02d}:{m:02d}"


runner = SimRunner()


# ----------------------------------------------------------------------
# API
# ----------------------------------------------------------------------

@app.get("/api/state")
def get_state(since: int = 0):
    return runner.snapshot(since_seq=since)


@app.post("/api/reset")
def reset():
    runner.reset(default_scenario())
    return runner.snapshot()


@app.post("/api/pause")
def pause():
    with runner.lock:
        runner.running = False
    return {"running": False}


@app.post("/api/resume")
def resume():
    with runner.lock:
        runner.running = True
    return {"running": True}


@app.post("/api/step")
def step_once():
    with runner.lock:
        runner.engine.step()
    return runner.snapshot()


class SpeedBody(BaseModel):
    delay_seconds: float


@app.post("/api/speed")
def set_speed(body: SpeedBody):
    with runner.lock:
        runner.delay = max(0.05, min(3.0, body.delay_seconds))
    return {"delay_seconds": runner.delay}


@app.post("/api/trigger_fault")
def trigger_fault():
    with runner.lock:
        runner.engine.trigger_fault()
    return runner.snapshot()


class NewLoadBody(BaseModel):
    name: str | None = None


@app.post("/api/add_load")
def add_load(body: NewLoadBody):
    with runner.lock:
        n = len(runner.engine.loads) + 1
        name = body.name or f"Load{n}"
        runner.engine.add_laundry_load(name)
    return runner.snapshot()


class NewRoomBody(BaseModel):
    room: str


@app.post("/api/add_dirty_room")
def add_dirty_room(body: NewRoomBody):
    with runner.lock:
        runner.engine.add_dirty_room(body.room)
    return runner.snapshot()


class PriceSpikeBody(BaseModel):
    amount: float = 300.0


@app.post("/api/price_spike")
def price_spike(body: PriceSpikeBody):
    with runner.lock:
        runner.engine.price_spike(body.amount)
    return runner.snapshot()


# ----------------------------------------------------------------------
# Static frontend
# ----------------------------------------------------------------------

app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.get("/")
def index():
    return FileResponse(str(FRONTEND_DIR / "index.html"))
