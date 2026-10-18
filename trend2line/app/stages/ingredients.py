"""Ingredient resolution over the Open Food Facts taxonomy. Deterministic, no LLM.

The taxonomy gives every ingredient multilingual names, parent links and allergen tags, e.g.
  pistachio paste  ->  pistachio nuts  ->  tree nut   (allergen en:nuts)
so the allergens of any ingredient are the tags on it and its ancestors, plus any allergen whose
synonyms (from the allergen taxonomy) match the names on that chain.

Resolution order for a free-text ingredient name:
  1. data/ingredient_overrides.csv  (curated gaps, e.g. kataifi -> wheat flour), quality "curated"
  2. exact name in any language                                               quality "exact"
  3. singular/plural or punctuation variant                                    quality "variant"
  4. longest trailing / leading word span that is a known ingredient           quality "partial"
Anything else is "unresolved" and is shown to the user; it never silently disappears.
"""
from __future__ import annotations

import csv
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from ..config import DATA_DIR

LANG_LINE = re.compile(r"^([a-z]{2,3}|xx):\s*(.*)$")
PROP_LINE = re.compile(r"^([a-z_0-9]+):([a-z]{2,3}|xx):\s*(.*)$")
PARENT_LINE = re.compile(r"^<\s*([a-z]{2,3}|xx):\s*(.*)$")


def norm(text: str) -> str:
    """Lowercase, strip accents (Latin scripts only), unify punctuation and spaces."""
    t = unicodedata.normalize("NFKD", text.strip().lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[_\-‐–—/]+", " ", t)
    t = re.sub(r"[\"'’`.,;:()\[\]{}!?*%]+", "", t)
    return re.sub(r"\s+", " ", t).strip()


def node_id(lang: str, name: str) -> str:
    return f"{lang}:{norm(name).replace(' ', '-')}"


@dataclass
class Node:
    id: str
    names: dict[str, list[str]] = field(default_factory=dict)
    parent_refs: list[tuple[str, str]] = field(default_factory=list)
    parents: list[str] = field(default_factory=list)
    allergens: set[str] = field(default_factory=set)

    @property
    def label(self) -> str:
        for lang in ("en", "xx"):
            if self.names.get(lang):
                return self.names[lang][0]
        return next(iter(self.names.values()))[0]


def _parse_blocks(path: Path):
    block: list[str] = []
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n")
            if not line.strip():
                if block:
                    yield block
                    block = []
                continue
            if line.lstrip().startswith("#"):
                continue
            block.append(line.strip())
    if block:
        yield block


def parse_taxonomy(path: Path) -> dict[str, Node]:
    nodes: dict[str, Node] = {}
    for block in _parse_blocks(path):
        if block[0].startswith(("stopwords:", "synonyms:")):
            continue
        names: dict[str, list[str]] = {}
        parents: list[tuple[str, str]] = []
        props: list[tuple[str, str]] = []
        for line in block:
            if m := PARENT_LINE.match(line):
                parents.append((m.group(1), m.group(2)))
            elif m := PROP_LINE.match(line):
                props.append((m.group(1), m.group(3)))
            elif m := LANG_LINE.match(line):
                vals = [v.strip() for v in m.group(2).split(",") if v.strip()]
                if vals:
                    names.setdefault(m.group(1), []).extend(vals)
        if not names:
            continue
        lang = "en" if "en" in names else ("xx" if "xx" in names else next(iter(names)))
        nid = node_id("en" if lang == "xx" else lang, names[lang][0])
        node = nodes.get(nid) or Node(nid)
        for k, v in names.items():
            node.names.setdefault(k, []).extend(v)
        node.parent_refs.extend(parents)
        for key, value in props:
            if key == "allergens":
                node.allergens |= {a.strip() for a in value.split(",") if a.strip()}
        nodes[nid] = node
    return nodes


class Taxonomy:
    def __init__(self, ingredients_path: Path, allergens_path: Path, overrides_path: Path | None = None):
        self.nodes = parse_taxonomy(ingredients_path)
        self.allergen_nodes = parse_taxonomy(allergens_path)
        self.index: dict[str, str] = {}
        self.index_lang: dict[str, str] = {}
        # two passes: English/universal names win over every other language, across all nodes
        for english_pass in (True, False):
            for nid, node in self.nodes.items():
                for lang in sorted(node.names):
                    if (lang in ("en", "xx")) != english_pass:
                        continue
                    for name in node.names[lang]:
                        key = norm(name)
                        if key and key not in self.index:
                            self.index[key] = nid
                            self.index_lang[key] = lang
        for node in self.nodes.values():
            for lang, name in node.parent_refs:
                pid = self.index.get(norm(name))
                if pid and pid != node.id:
                    node.parents.append(pid)
        # allergen synonyms (only 'none'-type blocks are skipped)
        self.allergen_syn: dict[str, str] = {}
        for aid, anode in self.allergen_nodes.items():
            if aid in ("en:none",):
                continue
            for name in anode.names.get("en", []) + anode.names.get("xx", []):
                self.allergen_syn.setdefault(norm(name), aid)
        self.overrides = self._load_overrides(overrides_path)

    def _load_overrides(self, path: Path | None) -> dict[str, dict]:
        if not path or not path.exists():
            return {}
        out = {}
        with open(path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                targets = [t.strip() for t in r["maps_to"].split("|") if t.strip()]
                out[norm(r["name"])] = {"maps_to": targets, "note": r.get("note", ""), "source": r.get("source", "")}
        return out

    # ------------------------------------------------------------------ lookup
    def ancestors(self, nid: str) -> list[str]:
        seen, stack, out = set(), [nid], []
        while stack:
            cur = stack.pop()
            if cur in seen or cur not in self.nodes:
                continue
            seen.add(cur)
            out.append(cur)
            stack.extend(self.nodes[cur].parents)
        return out

    def allergens_of(self, nid: str) -> set[str]:
        tags: set[str] = set()
        for a in self.ancestors(nid):
            node = self.nodes[a]
            tags |= node.allergens
            for name in node.names.get("en", [])[:6]:
                if aid := self.allergen_syn.get(norm(name)):
                    tags.add(aid)
        return tags

    def _variants(self, key: str):
        yield key
        if key.endswith("ies"):
            yield key[:-3] + "y"
        if key.endswith("es"):
            yield key[:-2]
        if key.endswith("s"):
            yield key[:-1]
        else:
            yield key + "s"

    def _known(self, key: str) -> str | None:
        for v in self._variants(key):
            if v in self.index:
                return self.index[v]
        return None

    def lookup(self, text: str) -> tuple[str | None, str]:
        key = norm(text)
        if not key:
            return None, "unresolved"
        english = ("en", "xx")
        if key in self.index and self.index_lang[key] in english:
            return self.index[key], "exact"
        for v in self._variants(key):  # English singular/plural beats an exact hit in another language
            if v != key and v in self.index and self.index_lang[v] in english:
                return self.index[v], "variant"
        if key in self.index:
            return self.index[key], "other_language"
        if nid := self._known(key):
            return nid, "variant"
        return None, "unresolved"

    def spans(self, text: str) -> list[tuple[str, list[str]]]:
        """Greedy longest-first cover of a name by known spans (overrides first, then taxonomy names).
        'toasted kataifi pastry' -> kataifi (override) + pastry; 'pistachio cream' -> pistachio + cream."""
        words = norm(text).split()
        used = [False] * len(words)
        found: list[tuple[str, list[str]]] = []
        for size in range(len(words), 0, -1):
            for i in range(0, len(words) - size + 1):
                if any(used[i:i + size]):
                    continue
                span = " ".join(words[i:i + size])
                if len(span) < 3:
                    continue
                ids: list[str] = []
                if span in self.overrides:
                    ids = [t for t in self.overrides[span]["maps_to"] if t in self.nodes]
                elif nid := self._known(span):
                    ids = [nid]
                if ids:
                    found.append((span, ids))
                    for k in range(i, i + size):
                        used[k] = True
        return found

    def keyword_allergens(self, text: str) -> set[str]:
        """Allergen synonyms that appear as whole words in the text (with simple plural variants)."""
        words = norm(text)
        hits = set()
        for syn, aid in self.allergen_syn.items():
            if not syn or len(syn) < 3:
                continue
            for v in {syn, syn.rstrip("s"), syn + "s"}:
                if re.search(rf"(?<!\w){re.escape(v)}(?!\w)", words):
                    hits.add(aid)
                    break
        return hits

    def resolve(self, name: str, components: list[str] | None = None) -> dict:
        """Resolve one ingredient.

        `components` are simpler parts (e.g. 'wheat flour' for kataifi) that the product deconstructor may
        supply for compound ingredients. Their allergens are added (over-declaring is the safe direction)
        and listed separately, so the user sees which allergens came from the decomposition.
        Allergen words in the name that the taxonomy did not confirm become `review_flags`, never silence.
        """
        key = norm(name)
        if key in self.overrides:
            o = self.overrides[key]
            res = self._result(name, [t for t in o["maps_to"] if t in self.nodes], "curated",
                               note=o["note"], source=o["source"])
        else:
            nid, quality = self.lookup(name)
            if nid:
                res = self._result(name, [nid], quality)  # "other_language" = a guess, flagged downstream
            elif parts := self.spans(name):
                res = self._result(name, [i for _, ids in parts for i in ids], "partial")
                res["matched_spans"] = [p for p, _ in parts]
            else:
                res = self._result(name, [], "unresolved")

        comp_allergens: set[str] = set()
        comp_ids: list[str] = []
        for comp in components or []:
            cid, q = self.lookup(comp)
            if cid and q in ("exact", "variant"):
                comp_ids.append(cid)
                comp_allergens |= self.allergens_of(cid)
        if comp_ids:
            added = sorted(comp_allergens - set(res["allergens"]))
            res["component_ids"] = comp_ids
            res["allergens_from_components"] = added
            res["allergens"] = sorted(set(res["allergens"]) | comp_allergens)
            res["lineage"] += [a for c in comp_ids for a in self.ancestors(c) if a not in res["lineage"]]
            if res["match"] == "unresolved":
                res["match"] = "components"

        kw = self.keyword_allergens(name) - set(res["allergens"])
        res["review_flags"] = sorted(kw)
        return res

    def _result(self, name: str, ids: list[str], quality: str, note: str = "", source: str = "") -> dict:
        lineage: list[str] = []
        allergens: set[str] = set()
        for i in ids:
            for a in self.ancestors(i):
                if a not in lineage:
                    lineage.append(a)
            allergens |= self.allergens_of(i)
        return {
            "name": name,
            "taxonomy_ids": ids,
            "labels": [self.nodes[i].label for i in ids],
            "lineage": lineage,
            "allergens": sorted(allergens),
            "match": quality,
            **({"note": note} if note else {}),
            **({"source": source} if source else {}),
        }

    def allergen_label(self, aid: str) -> str:
        n = self.allergen_nodes.get(aid)
        return n.label if n else aid


@lru_cache
def taxonomy() -> Taxonomy:
    src = DATA_DIR / "sources"
    return Taxonomy(src / "off_ingredients.txt", src / "off_allergens.txt", DATA_DIR / "ingredient_overrides.csv")
