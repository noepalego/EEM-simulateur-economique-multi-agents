"""Lance realism_probe.run sur plusieurs variantes et graines ; résumés ajoutés en JSONL.

Usage : python batch_probe.py sortie.jsonl MOIS graines(1006,1007,...) variante1 variante2 ... [--jobs N]
Variante : nom=param:valeur;param:valeur, à partir de la configuration unique ;
« param:DEFAULT » rend au paramètre la valeur par défaut du moteur. Les couples (variante, graine) déjà présents dans le
fichier sont sautés : on peut relancer après une interruption. Chaque résumé porte
l'empreinte des sources (``source``) : une reprise avec un moteur modifié est signalée.
``--jobs`` : nombre de processus (8 par défaut, jamais plus que de couples à simuler).
"""
import json
import os
import sys
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import realism_probe  # noqa: E402


def parse(spec: str) -> tuple[str, dict]:
    name, _, rest = spec.partition("=")
    overrides = {}
    for kv in filter(None, rest.split(";")):
        k, v = kv.split(":", 1)
        if v == "DEFAULT":
            overrides.pop(k, None)
            continue
        try:
            overrides[k] = float(v) if "." in v else int(v)
        except ValueError:
            overrides[k] = v
    return name, overrides


def job(args):
    seed, months, name, overrides, source = args
    try:
        summary = realism_probe.run((seed, months, overrides, name, None))
        summary["variant"] = name
        summary["overrides"] = overrides
        summary["source"] = source
        return summary
    except Exception as exc:  # une variante qui plante est un résultat
        return {"variant": name, "seed": seed, "error": repr(exc), "overrides": overrides, "source": source}


if __name__ == "__main__":
    argv = sys.argv[1:]
    jobs_count = 8
    if "--jobs" in argv:
        index = argv.index("--jobs")
        jobs_count = int(argv[index + 1])
        del argv[index:index + 2]
    out, months, seeds, *specs = argv
    seeds = [int(s) for s in seeds.split(",")]
    source = realism_probe.source_fingerprint()
    done = set()
    if os.path.exists(out):
        with open(out, encoding="utf-8") as fh:
            for line in fh:
                r = json.loads(line)
                done.add((r["variant"], r["seed"]))
                if r.get("source", source) != source:
                    print("attention : résultat existant produit par d'autres sources", r["variant"], r["seed"])
    variants = [parse(s) for s in specs]
    jobs = [(seed, int(months), name, ov, source) for name, ov in variants for seed in seeds
            if (name, seed) not in done]
    with Pool(max(1, min(jobs_count, len(jobs))), maxtasksperchild=1) as pool, \
            open(out, "a", encoding="utf-8") as fh:
        for result in pool.imap_unordered(job, jobs):
            fh.write(json.dumps(result) + "\n")
            fh.flush()
    print("ok", len(jobs), "sources", source)
