"""Time to shelf = longest path through a small task DAG. Dates come from here, never the LLM."""
import datetime as dt


def schedule(tasks: dict[str, tuple[int, list[str]]]) -> dict:
    finish: dict[str, int] = {}
    visiting: set[str] = set()

    def f(name: str) -> int:
        if name in finish:
            return finish[name]
        if name in visiting:
            raise ValueError(f"cycle at {name}")
        visiting.add(name)
        dur, deps = tasks[name]
        finish[name] = dur + max((f(d) for d in deps), default=0)
        visiting.discard(name)
        return finish[name]

    for t in tasks:
        f(t)
    total = max(finish.values())
    end = max(finish, key=finish.get)
    path = [end]
    while True:
        dur, deps = tasks[path[-1]]
        start = finish[path[-1]] - dur
        prev = [d for d in deps if finish[d] == start]
        if not prev:
            break
        path.append(prev[0])
    rows = [{"task": t, "start_day": finish[t] - tasks[t][0], "end_day": finish[t],
             "duration_days": tasks[t][0], "critical": t in path} for t in tasks]
    rows.sort(key=lambda r: (r["start_day"], r["end_day"]))
    return {"total_days": total, "critical_path": list(reversed(path)), "tasks": rows}


def build_route(route: str, cfg: dict, gap_days: int = 0, needs_validation: bool = False,
                localisation: bool = False) -> dict[str, tuple[int, list[str]]]:
    """Task DAG for a route. `localisation` adds label translation/approval when the market needs a label
    language the company does not already print; it runs in parallel with artwork and gates ramp-up."""
    tasks = _route(route, cfg, gap_days, needs_validation)
    if localisation and cfg.get("label_localisation"):
        tasks["label_localisation"] = (cfg["label_localisation"], ["trials"])
        dur, deps = tasks["ramp_up"]
        tasks["ramp_up"] = (dur, deps + ["label_localisation"])
    return tasks


def _route(route: str, cfg: dict, gap_days: int, needs_validation: bool) -> dict[str, tuple[int, list[str]]]:
    if route == "in_house":
        c = cfg["in_house"]
        ramp_deps = ["label_artwork", "supplier_qualification"]
        tasks = {
            "gap_resolution": (gap_days, []),
            "supplier_qualification": (c["supplier_qualification"], []),
            "trials": (c["trials"], ["gap_resolution"]),
            "label_artwork": (c["label_artwork"], ["trials"]),
        }
        if needs_validation:
            tasks["allergen_cleaning_validation"] = (c["allergen_cleaning_validation"], ["trials"])
            ramp_deps.append("allergen_cleaning_validation")
        tasks["ramp_up"] = (c["ramp_up"], ramp_deps)
        tasks["distribution_fill"] = (c["distribution_fill"], ["ramp_up"])
        return tasks
    c = cfg["co_manufacturer"]
    return {
        "onboarding": (c["onboarding"], []),
        "trials": (c["trials"], ["onboarding"]),
        "label_artwork": (c["label_artwork"], ["trials"]),
        "ramp_up": (c["ramp_up"], ["label_artwork"]),
        "distribution_fill": (c["distribution_fill"], ["ramp_up"]),
    }


def launch_date(run_date: dt.date, total_days: int) -> str:
    return (run_date + dt.timedelta(days=total_days)).isoformat()
