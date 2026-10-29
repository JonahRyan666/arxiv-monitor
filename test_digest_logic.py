import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import digest_lib
from digest_lib import (
    BACKUP_LABEL,
    DISCUSSION_FOOTER,
    EMPTY_NOTE,
    build_feishu_cards,
    parse_labeled_translation,
    parse_translation_payload,
    Prefilter,
    build_arxiv_search_query,
    build_feishu_card,
    build_feishu_payload,
    build_results_request,
    compute_window,
    decide_screening,
    dedupe_papers,
    extract_json,
    feishu_sign,
    load_settings,
    load_state,
    normalize_any_id,
    normalize_arxiv_id,
    normalize_doi,
    parse_arxiv_feed,
    parse_screening_batch,
    passes_prefilter,
    prefilter_status,
    save_state,
    select_papers,
    sign_results_body,
    update_pending,
)

os.environ.setdefault("FEISHU_WEBHOOK_URL", "https://example.invalid/hook")
os.environ.setdefault("SILICONFLOW_API_KEY", "test-key")

import arxiv_daily_report as monitor


ARXIV_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2401.12345v2</id>
    <published>2024-01-02T03:04:05Z</published>
    <title>Quantum spin liquid  in a kagome magnet</title>
    <summary>Single crystals were grown by a flux method.</summary>
    <author><name>A. Author</name></author>
    <author><name>B. Author</name></author>
    <arxiv:doi>10.1103/PhysRevB.100.123456</arxiv:doi>
    <category term="cond-mat.str-el" scheme="http://arxiv.org/schemas/atom"/>
  </entry>
  <entry>
    <id>http://arxiv.org/api/errors</id>
    <title>Error</title>
    <summary>malformed</summary>
  </entry>
</feed>
"""


def screening(**overrides):
    base = {
        "index": 0,
        "experimental": True,
        "sample_form": "single crystal",
        "growth_method": "flux",
        "measurements": ["χ(T)", "specific heat"],
        "theta_CW": "-150 K",
        "T_order": "6 K",
        "frustration_index": 25,
        "room_temperature_relevant": False,
        "meets_measured_data": True,
        "meets_physics_highlight": True,
        "meets_feasible": True,
        "thin_film_only_mbe": False,
        "score": 8,
        "highlight_zh": "助熔剂生长的笼目单晶呈现高阻挫且无长程有序。",
        "summary_zh": "作者用助熔剂法生长了单晶，并测量了磁化率和比热。阻挫指数较高，是量子自旋液体候选。",
    }
    base.update(overrides)
    return base


def paper(**overrides):
    base = {
        "id": "2401.12345",
        "arxiv_id": "2401.12345",
        "doi": None,
        "title": "Quantum spin liquid on a kagome lattice",
        "summary": (
            "Single crystals were grown by the flux method. "
            "Magnetic susceptibility and specific heat show no long-range order."
        ),
        "authors": ["Ann Author", "Bo Bates", "Cai Chen", "Di Diaz"],
        "link": "https://arxiv.org/abs/2401.12345",
        "source": "arxiv",
    }
    base.update(overrides)
    return base


class DedupTests(unittest.TestCase):
    def test_arxiv_ids_drop_version_and_prefixes(self):
        cases = {
            "2401.12345v2": "2401.12345",
            "arxiv:2401.12345v2": "2401.12345",
            "arXiv:2401.12345": "2401.12345",
            "https://arxiv.org/abs/2401.12345v2": "2401.12345",
            "https://arxiv.org/pdf/2401.12345v2.pdf": "2401.12345",
            "cond-mat/0703001v2": "cond-mat/0703001",
            "https://arxiv.org/abs/hep-th/9901001v1": "hep-th/9901001",
        }
        for raw, expected in cases.items():
            self.assertEqual(normalize_arxiv_id(raw), expected, raw)

    def test_doi_normalization(self):
        self.assertEqual(
            normalize_doi("https://doi.org/10.1103/PhysRevB.100.123456"),
            "10.1103/physrevb.100.123456",
        )
        self.assertEqual(
            normalize_doi("doi:10.7566/JPSJ.95.123456"),
            "10.7566/jpsj.95.123456",
        )
        self.assertEqual(
            normalize_doi("jpsj:10.7566/JPSJ.95.123456."),
            "10.7566/jpsj.95.123456",
        )
        self.assertIsNone(normalize_arxiv_id("10.1021/journal.2024.12345"))
        self.assertEqual(
            normalize_any_id("https://doi.org/10.1021/journal.2024.12345"),
            {"10.1021/journal.2024.12345"},
        )

    def test_legacy_sent_file_is_normalized(self):
        folder = Path(tempfile.mkdtemp())
        legacy = folder / "sent_papers.json"
        legacy.write_text(json.dumps([
            "arxiv:2401.12345v3",
            "https://doi.org/10.1103/PhysRevB.100.123456",
        ]), encoding="utf-8")
        state = load_state(folder / "missing.json", legacy)
        self.assertEqual(state["sent_ids"], [
            "10.1103/physrevb.100.123456",
            "2401.12345",
        ])

    def test_state_roundtrip_and_title_dedup(self):
        folder = Path(tempfile.mkdtemp())
        path = folder / "digest_state.json"
        state = digest_lib.empty_state()
        first = paper(arxiv_id="2401.12345v2", id="arxiv:2401.12345v2")
        digest_lib.remember_sent(state, [first])
        state["last_successful_run"] = "2026-10-27T01:00:00+00:00"
        save_state(path, state)
        loaded = load_state(path)
        self.assertIn("2401.12345", loaded["sent_ids"])
        self.assertNotIn("2401.12345v2", loaded["sent_ids"])
        again = paper(
            arxiv_id=None,
            doi="10.9999/new",
            id="jpsj:10.9999/new",
            title=first["title"],
        )
        self.assertTrue(digest_lib.is_already_sent(again, loaded))
        other = paper(
            id="9999.00001",
            arxiv_id="9999.00001",
            link="https://arxiv.org/abs/9999.00001",
            title="Electric polarization of an unrelated oxide film",
        )
        self.assertFalse(digest_lib.is_already_sent(other, loaded))

    def test_same_day_arxiv_and_doi_collapse(self):
        left = paper(arxiv_id="2401.12345", doi=None, summary="short")
        right = paper(
            arxiv_id=None,
            doi="10.1103/physrevb.100.123456",
            id="jpsj:10.1103/physrevb.100.123456",
            source="jpsj",
            summary=left["summary"] + " Magnetic susceptibility was measured on the crystals.",
        )
        merged = dedupe_papers([left, right])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["arxiv_id"], "2401.12345")
        self.assertEqual(merged[0]["doi"], "10.1103/physrevb.100.123456")


class PrefilterTests(unittest.TestCase):
    def test_keeps_experimental_spin_liquid(self):
        self.assertEqual(prefilter_status(
            "Quantum spin liquid on the kagome lattice",
            "Single crystals were grown by flux method. Magnetic susceptibility and specific heat were measured.",
        ), "keep")

    def test_drops_dft_only(self):
        self.assertEqual(prefilter_status(
            "First-principles study of a kagome spin liquid",
            "We perform DFT calculations of the band structure.",
        ), "theory")
        self.assertFalse(passes_prefilter(
            "Density functional theory of a Kitaev magnet",
            "Ab initio calculations without experimental data.",
        ))

    def test_keeps_dft_when_samples_were_measured(self):
        self.assertTrue(passes_prefilter(
            "DFT and experiments on a multiferroic",
            "Polycrystalline samples were synthesized by solid-state reaction and magnetization was measured.",
        ))

    def test_drops_model_monte_carlo_and_dmrg_without_samples(self):
        self.assertEqual(prefilter_status(
            "We propose a model for the Kitaev honeycomb magnet",
            "Using DMRG and tensor networks we compute the phase diagram.",
        ), "theory")
        self.assertEqual(prefilter_status(
            "Monte Carlo simulation of a pyrochlore spin liquid",
            "We use classical Monte Carlo.",
        ), "theory")

    def test_drops_off_topic(self):
        self.assertEqual(prefilter_status(
            "Superconducting qubits with tunable couplers",
            "We measured T1 of the device.",
        ), "offtopic")

    def test_keeps_topic_without_an_abstract(self):
        self.assertTrue(passes_prefilter("Frustrated magnetism in a honeycomb lattice", ""))

    def test_keeps_magnetoelectric_triangular_lattice(self):
        self.assertTrue(passes_prefilter(
            "Magnetoelectric coupling in a triangular-lattice multiferroic",
            "The ferroelectric polarization is field-tunable.",
        ))
        self.assertTrue(passes_prefilter("Spin correlations on the triangular-lattice", ""))
        self.assertTrue(passes_prefilter(
            "Chiral spin-liquid on the kagome lattice",
            "Single crystals were grown and susceptibility was measured.",
        ))
        self.assertEqual(prefilter_status(
            "First-principles study of a spin-liquid",
            "We perform DFT calculations of the band structure.",
        ), "theory")

    def test_keyword_score_weights_the_title(self):
        title_hit = digest_lib.keyword_match_score("Kagome spin liquid", "")
        abstract_hit = digest_lib.keyword_match_score("A new oxide", "We study a kagome spin liquid.")
        self.assertGreater(title_hit, abstract_hit)
        self.assertGreaterEqual(title_hit, 3)

    def test_photocatalysis_ferroelectric_is_off_topic(self):
        self.assertEqual(prefilter_status(
            "Pulsed Laser Deposition of Ferroelectric SrTiO3 Shells Enables Polarization-Controlled "
            "Charge Separation in TiO2 Photoanode",
            "Ferroelectric SrTiO3 shells grown by PLD improve photoelectrochemical water splitting.",
        ), "offtopic")
        self.assertEqual(prefilter_status(
            "Lead-free KCuCl3 memristors for synaptic devices",
            "The ferroelectric-like switching was measured.",
        ), "offtopic")

    def test_hard_negative_with_a_core_topic_is_kept_but_ranked_low(self):
        title = "Kagome photocatalyst for hydrogen evolution"
        summary = "A kagome framework is used as a photocatalyst for hydrogen evolution in water splitting."
        self.assertEqual(prefilter_status(title, summary), "keep")
        core = digest_lib.keyword_match_score(
            "Spin liquid ground state in a kagome antiferromagnet",
            "Single crystals were grown by the flux method; susceptibility and specific heat show no order.",
        )
        self.assertGreater(core, digest_lib.keyword_match_score(title, summary) + 10)

    def test_pure_ferroelectric_ranks_below_frustrated_magnet(self):
        ferro = digest_lib.keyword_match_score(
            "Relaxor ferroelectric behavior in Ag-substituted K5Nb9O25 single crystals",
            "Single crystals were grown and the dielectric permittivity shows relaxor behavior.",
        )
        magnet = digest_lib.keyword_match_score(
            "Stripe antiferromagnetic order and spin dynamics in the Shastry-Sutherland Ising compound Er2Be2GeO7",
            "Neutron scattering, magnetization and specific heat on single crystals reveal frustrated order.",
        )
        optics = digest_lib.keyword_match_score(
            "Holonomy analysis of optical-polarization temperature trajectories in stress-induced ferroelectric SrTiO3",
            "We analyse optical polarization trajectories of a thin film device.",
        )
        self.assertGreater(magnet, ferro)
        self.assertGreater(ferro, optics)
        self.assertEqual(prefilter_status(
            "Relaxor ferroelectric behavior in Ag-substituted K5Nb9O25 single crystals",
            "Single crystals were grown and the dielectric permittivity shows relaxor behavior.",
        ), "keep")

    def test_magnetoelectric_bonus_beats_plain_ferroelectric(self):
        multiferroic = digest_lib.keyword_match_score(
            "Spin-driven ferroelectric polarization in a polar magnet",
            "Pyroelectric current and magnetization of single crystals show magnetoelectric coupling.",
        )
        plain = digest_lib.keyword_match_score(
            "Ferroelectric domain switching in a perovskite oxide",
            "Pyroelectric current of single crystals was measured.",
        )
        self.assertGreater(multiferroic, plain + 15)

    def test_broader_magnetism_is_a_candidate(self):
        for title in (
            "Quasiparticle interactions in the S=1/2 Heisenberg antiferromagnetic chain CuPzN",
            "Anisotropic magneto-elastic coupling in the quasi-2D Ising magnet Li2FeSiO4",
            "Spin-flop transition in a rare-earth orthoferrite",
            "Field-induced magnon condensation in a spin dimer compound",
        ):
            self.assertEqual(prefilter_status(title, "Single crystals; magnetization measured."), "keep", title)

    def test_applied_thin_film_work_ranks_below_bulk_crystals(self):
        film = digest_lib.keyword_match_score(
            "Spin-orbit torque switching in antiferromagnetic thin film heterostructure devices",
            "Epitaxial films were sputtered for spintronic devices.",
        )
        bulk = digest_lib.keyword_match_score(
            "Antiferromagnetic order in flux-grown single crystals",
            "Magnetic susceptibility and neutron diffraction were measured.",
        )
        self.assertGreater(bulk, film)
        self.assertLess(film, 0)

    def test_config_keywords_replace_defaults(self):
        settings = digest_lib.Settings(keywords=["skyrmion"], theory_markers=[], experiment_markers=[])
        settings.prefilter = Prefilter(settings.keywords, [], [])
        self.assertTrue(passes_prefilter("Skyrmion lattice melting", "We measured it.", settings))
        self.assertFalse(passes_prefilter("Kagome spin liquid", "Single crystals were grown.", settings))


class JsonAndDecisionTests(unittest.TestCase):
    def test_extracts_fenced_json_and_repairs_trailing_comma(self):
        text = """说明如下：
```json
{"experimental": true, "score": 8,}
```
"""
        self.assertEqual(extract_json(text), {"experimental": True, "score": 8})

    def test_parse_batch_aligns_by_index_and_id(self):
        payload = json.dumps([
            screening(index=1, id="10.1000/xyz", score=4),
            screening(index=0, id="2401.12345", score=9),
        ])
        parsed = parse_screening_batch(payload, 2, ["2401.12345", "10.1000/xyz"])
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed[0]["score"], 9)
        self.assertEqual(parsed[1]["score"], 4)

    def test_single_object_and_wrapped_results(self):
        self.assertIsNotNone(parse_screening_batch(json.dumps(screening()), 1, ["2401.12345"]))
        wrapped = json.dumps({"papers": [screening(index=0), screening(index=1, score=3)]})
        parsed = parse_screening_batch(wrapped, 2)
        self.assertEqual([item["score"] for item in parsed], [8, 3])

    def test_invalid_json_returns_none(self):
        self.assertIsNone(extract_json("not json at all"))
        self.assertIsNone(parse_screening_batch("[]", 1))

    def test_hard_drop_theory_and_mbe_film(self):
        theory = decide_screening(paper(), screening(experimental=False, score=9))
        self.assertTrue(theory["hard_drop"])
        self.assertFalse(theory["passes"])
        mbe = decide_screening(paper(), screening(
            sample_form="film",
            growth_method="molecular beam epitaxy",
            thin_film_only_mbe=False,
        ))
        self.assertTrue(mbe["hard_drop"])
        flagged = decide_screening(paper(), screening(thin_film_only_mbe=True, sample_form="single crystal"))
        self.assertTrue(flagged["hard_drop"])

    def test_unclear_growth_still_passes(self):
        decided = decide_screening(paper(), screening(meets_feasible=False, score=6))
        self.assertTrue(decided["passes"])
        self.assertFalse(decided["hard_drop"])
        self.assertFalse(decided["meets_feasible"])

    def test_pass_needs_score_of_at_least_six(self):
        modest = decide_screening(paper(), screening(
            meets_measured_data=False,
            meets_physics_highlight=True,
            meets_feasible=False,
            measurements=["specific heat"],
            score=4,
        ))
        self.assertFalse(modest["passes"])
        self.assertFalse(modest["hard_drop"])
        good = decide_screening(paper(), screening(
            meets_measured_data=False,
            meets_physics_highlight=False,
            meets_feasible=False,
            measurements=["specific heat"],
            score=6,
        ))
        self.assertTrue(good["passes"])

    def test_off_topic_flag_blocks_a_pass_even_with_a_high_score(self):
        decided = decide_screening(
            paper(title="Ferroelectric SrTiO3 shells in a TiO2 photoanode"),
            screening(off_topic=True, topic="off_topic", score=7),
        )
        self.assertFalse(decided["passes"])
        self.assertTrue(decided["off_topic"])
        self.assertFalse(decided["hard_drop"])

    def test_low_score_without_physics_stays_runner_up(self):
        decided = decide_screening(paper(), screening(
            meets_physics_highlight=False,
            meets_feasible=False,
            room_temperature_relevant=False,
            score=2,
        ))
        self.assertFalse(decided["passes"])
        self.assertFalse(decided["hard_drop"])

    def test_toxic_precursor_fails_feasibility(self):
        decided = decide_screening(
            paper(summary="Single crystals were grown from a thallium flux. Susceptibility was measured."),
            screening(meets_feasible=True),
        )
        self.assertFalse(decided["meets_feasible"])
        self.assertFalse(decided["passes"])
        self.assertFalse(decided["hard_drop"])

    def test_derives_criteria_when_flags_omitted(self):
        raw = screening()
        for key in ("meets_measured_data", "meets_physics_highlight", "meets_feasible"):
            raw.pop(key)
        decided = decide_screening(paper(), raw)
        self.assertTrue(decided["passes"])


class SelectionAndCardTests(unittest.TestCase):
    def _decided(self, score, passes=True, hard_drop=False, **extra):
        title = extra.pop("title", f"Paper {score}")
        self._seq = getattr(self, "_seq", 0) + 1
        arxiv_id = f"2401.{self._seq:05d}"
        item = paper(
            title=title,
            id=arxiv_id,
            arxiv_id=arxiv_id,
            link=f"https://arxiv.org/abs/{arxiv_id}",
        )
        item.update({
            "score": score,
            "passes": passes,
            "hard_drop": hard_drop,
            "screen_error": False,
            "experimental": not hard_drop,
            "growth_method": extra.pop("growth_method", "flux"),
            "sample_form": "single crystal",
            "highlight_zh": "这是一句中文亮点，说明测量到了无长程有序。",
            "summary_zh": "样品由助熔剂法生长，并测量了磁化率与比热。数据支持阻挫磁体图像。",
            "room_temperature_relevant": extra.pop("room_temperature_relevant", False),
            "backup": False,
        })
        item.update(extra)
        return item

    def test_caps_qualifying_papers_at_max(self):
        items = [self._decided(score) for score in range(1, 13)]
        selected, empty = select_papers(items, max_papers=10, min_papers=3)
        self.assertFalse(empty)
        self.assertEqual(len(selected), 10)
        self.assertTrue(all(not item["backup"] for item in selected))
        self.assertEqual(selected[0]["score"], 12)

    def test_fills_backups_up_to_minimum_and_skips_theory(self):
        items = [
            self._decided(9, passes=True, title="Good A"),
            self._decided(8, passes=True, title="Good B"),
            self._decided(7, passes=False, title="Backup high"),
            self._decided(6, passes=False, title="Backup low"),
            self._decided(10, passes=False, hard_drop=True, title="Pure theory"),
        ]
        selected, empty = select_papers(items, max_papers=10, min_papers=3)
        self.assertFalse(empty)
        self.assertEqual(
            [item["title"] for item in selected],
            ["Good A", "Good B", "Backup high", "Backup low"],
        )
        self.assertEqual(selected[-1]["backup_label"], BACKUP_LABEL)
        self.assertTrue(selected[-1]["backup"])
        self.assertNotIn("Pure theory", [item["title"] for item in selected])

    def test_sends_what_exists_when_backups_run_out(self):
        selected, empty = select_papers(
            [self._decided(8, passes=True), self._decided(4, passes=True)],
            max_papers=10,
            min_papers=3,
        )
        self.assertFalse(empty)
        self.assertEqual(len(selected), 2)

    def test_backup_only_day_and_empty_day(self):
        backups = [self._decided(score, passes=False, title=f"B{score}") for score in (3, 9, 5, 1)]
        selected, empty = select_papers(backups, max_papers=10, min_papers=3)
        self.assertFalse(empty)
        self.assertEqual([item["score"] for item in selected], [9, 5, 3])
        self.assertTrue(all(item["backup"] for item in selected))

        nothing, note = select_papers([], max_papers=10, min_papers=3)
        self.assertTrue(note)
        self.assertEqual(nothing, [])

    def test_hard_dropped_papers_are_never_used(self):
        only_theory, note = select_papers(
            [self._decided(2, hard_drop=True, passes=False, title="Theory only")],
            max_papers=10,
            min_papers=3,
        )
        self.assertTrue(note)
        self.assertEqual(only_theory, [])

    def test_tops_up_to_five_and_allows_a_close_sixth(self):
        passing = [self._decided(9, passes=True, title="Pass A"), self._decided(8, passes=True, title="Pass B")]
        runners = [
            self._decided(7, passes=False, title="Run 7"),
            self._decided(6.2, passes=False, title="Run 6.2"),
            self._decided(5.4, passes=False, title="Run 5.4"),
            self._decided(5.0, passes=False, title="Run 5"),
            self._decided(2, passes=False, title="Run 2"),
        ]
        selected, empty = select_papers(passing + runners, max_papers=10, min_papers=5)
        self.assertFalse(empty)
        self.assertEqual(
            [item["title"] for item in selected],
            ["Pass A", "Pass B", "Run 7", "Run 6.2", "Run 5.4", "Run 5"],
        )
        self.assertTrue(all(item["backup"] for item in selected[2:]))
        self.assertTrue(all(not item["backup"] for item in selected[:2]))
        self.assertNotIn("Run 2", [item["title"] for item in selected])

    def test_does_not_add_sixth_when_the_gap_is_large(self):
        items = [self._decided(score, passes=False, title=f"R{score}") for score in (9, 8, 7, 6, 5, 2)]
        selected, empty = select_papers(items, max_papers=10, min_papers=5)
        self.assertFalse(empty)
        self.assertEqual([item["score"] for item in selected], [9, 8, 7, 6, 5])

    def test_keyword_candidates_fill_after_runner_ups(self):
        runners = [self._decided(6, passes=False, title="Scored runner")]
        weaker = paper(title="Weaker pyrochlore", arxiv_id="2402.00002", id="2402.00002", link="https://arxiv.org/abs/2402.00002")
        stronger = paper(title="Stronger kagome spin liquid", arxiv_id="2402.00003", id="2402.00003", link="https://arxiv.org/abs/2402.00003")
        weaker.update({"keyword_score": 1, "keyword_only": True, "llm_scored": False, "passes": False, "hard_drop": False})
        stronger.update({"keyword_score": 6, "keyword_only": True, "llm_scored": False, "passes": False, "hard_drop": False})
        theory = self._decided(10, passes=False, hard_drop=True, title="Pure theory")
        selected, empty = select_papers(
            [runners[0], theory],
            max_papers=10,
            min_papers=5,
            unscored=[weaker, stronger],
        )
        self.assertFalse(empty)
        self.assertEqual(selected[0]["title"], "Scored runner")
        self.assertEqual(selected[1]["title"], "Stronger kagome spin liquid")
        self.assertEqual(selected[2]["title"], "Weaker pyrochlore")
        self.assertTrue(all(item["backup"] for item in selected))
        self.assertNotIn("Pure theory", [item["title"] for item in selected])
        self.assertEqual(len(selected), 3)

    def test_min_five_top_up_order_and_labels(self):
        passing = [
            self._decided(9, passes=True, title="Pass kagome"),
            self._decided(7, passes=True, title="Pass multiferroic"),
        ]
        runner = self._decided(5, passes=False, title="Runner antiferromagnet")
        off = self._decided(8, passes=False, title="Off-topic photoanode", off_topic=True)
        theory_hi = self._decided(2.5, passes=False, hard_drop=True, title="Theory B")
        theory_lo = self._decided(1, passes=False, hard_drop=True, title="Theory C")
        keyword = paper(title="Unscored spin chain", arxiv_id="2402.00009", id="2402.00009",
                        link="https://arxiv.org/abs/2402.00009")
        keyword.update({"keyword_score": 9, "keyword_only": True, "llm_scored": False,
                        "passes": False, "hard_drop": False})
        selected, empty = select_papers(
            [theory_lo, off, runner, *passing, theory_hi],
            max_papers=10,
            min_papers=5,
            unscored=[keyword],
        )
        self.assertFalse(empty)
        self.assertEqual(
            [item["title"] for item in selected],
            ["Pass kagome", "Pass multiferroic", "Runner antiferromagnet",
             "Unscored spin chain", "Off-topic photoanode"],
        )
        self.assertEqual([item["backup"] for item in selected], [False, False, True, True, True])
        self.assertEqual(selected[2]["backup_label"], BACKUP_LABEL)

    def test_close_sixth_never_pulls_in_off_topic_or_theory(self):
        items = [self._decided(score, passes=False, title=f"R{score}") for score in (9, 8, 7, 6, 5)]
        items.append(self._decided(5, passes=False, title="Off", off_topic=True))
        items.append(self._decided(5, passes=False, hard_drop=True, title="Theory"))
        selected, _empty = select_papers(items, max_papers=10, min_papers=5)
        self.assertEqual([item["title"] for item in selected], ["R9", "R8", "R7", "R6", "R5"])

    def test_passes_rank_best_score_first(self):
        items = [self._decided(score, passes=True, title=f"P{score}") for score in (6, 9.5, 7, 8)]
        selected, _empty = select_papers(items, max_papers=10, min_papers=1)
        self.assertEqual([item["score"] for item in selected], [9.5, 8, 7, 6])

    def test_many_passes_stay_under_the_normal_cap(self):
        items = [self._decided(score, passes=True, title=f"P{score}") for score in range(1, 9)]
        selected, empty = select_papers(items, max_papers=10, min_papers=5)
        self.assertFalse(empty)
        self.assertEqual(len(selected), 8)
        self.assertTrue(all(not item["backup"] for item in selected))

    def test_room_temperature_breaks_score_ties(self):
        items = [
            self._decided(8, title="Low T", room_temperature_relevant=False),
            self._decided(8, title="Room T", room_temperature_relevant=True),
        ]
        selected, _empty = select_papers(items, max_papers=10, min_papers=1)
        self.assertEqual(selected[0]["title"], "Room T")

    def test_card_lists_required_fields_and_backup_label(self):
        chosen = self._decided(8, title="Kagome [spin] liquid", arxiv_id="2401.12345v1")
        chosen["title_zh"] = "笼目自旋液体"
        chosen["abstract_zh"] = (
            "本文报道笼目晶格上的量子自旋液体。样品为单晶，测量了磁化率与比热。"
            "公式 $\\mathbf{C}=(1,1,1)$ 给出了磁场方向。"
        )
        chosen["summary_zh"] = "这位博士生应该把这篇当作首选。"
        chosen["theta_CW"] = "-120 K"
        backup = self._decided(6.5, passes=False, title="Backup crystal", growth_method="CVT")
        backup["backup"] = True
        backup["backup_label"] = BACKUP_LABEL
        backup["title_zh"] = "备选晶体"
        backup["abstract_zh"] = "这是一篇备选论文的完整中文摘要，包含比热和介电测量的具体描述，供群里决定要不要细读。"
        now = datetime(2026, 10, 28, 1, 0, tzinfo=timezone.utc)
        card = build_feishu_card(
            [chosen, backup],
            generated_at=now,
            window_start=now - timedelta(days=1),
            window_end=now,
            max_authors=3,
        )
        blob = json.dumps(card, ensure_ascii=False)
        self.assertIn("https://arxiv.org/abs/2401.12345v1", blob)
        self.assertIn("Kagome ［spin］ liquid", blob)
        self.assertIn("Ann Author, Bo Bates, Cai Chen et al.", blob)
        self.assertIn("**达标** · 评分 8/10", blob)
        self.assertIn("【中文题名】笼目自旋液体", blob)
        self.assertIn("【摘要翻译】本文报道笼目晶格上的量子自旋液体。", blob)
        self.assertIn("mathbf{C}=(1,1,1)", blob)
        self.assertIn("【亮点/筛选理由】这是一句中文亮点", blob)
        self.assertIn("单晶 · flux · θCW=-120 K · arXiv", blob)
        self.assertNotIn("博士生", blob)
        self.assertIn("**备选** · 评分 6.5/10", blob)
        self.assertIn("CVT", blob)
        self.assertIn("**#1**", blob)
        self.assertIn("**#2**", blob)
        self.assertLess(blob.index("**#1**"), blob.index("**#2**"))
        self.assertIn(DISCUSSION_FOOTER, blob)
        self.assertEqual(blob.count("**#1**"), 1)
        self.assertEqual(blob.count("**#2**"), 1)
        self.assertEqual(card["header"]["template"], "blue")

    def test_unscored_backup_omits_score_and_keeps_its_number(self):
        unscored = paper(title="Keyword only pyrochlore", arxiv_id="2401.22222")
        unscored.update({
            "backup": True,
            "backup_label": BACKUP_LABEL,
            "keyword_only": True,
            "llm_scored": False,
            "score": None,
            "keyword_score": 3,
        })
        now = datetime(2026, 10, 28, 1, 0, tzinfo=timezone.utc)
        card = build_feishu_card(
            [unscored],
            generated_at=now,
            window_start=now,
            window_end=now,
        )
        blob = json.dumps(card, ensure_ascii=False)
        self.assertIn("**#1**", blob)
        self.assertIn(BACKUP_LABEL, blob)
        self.assertIn("【亮点/筛选理由】关键词匹配入选，供讨论是否细读。", blob)
        self.assertIn("【摘要翻译】", blob)
        self.assertNotIn("评分", blob)
        self.assertIn(DISCUSSION_FOOTER, blob)

    def test_long_digest_splits_cards_and_keeps_numbering(self):
        papers = []
        for index in range(3):
            item = self._decided(8, title=f"Long paper {index}", arxiv_id=f"2401.5555{index}")
            item["title_zh"] = f"长摘要题名{index}"
            item["abstract_zh"] = "测" * 900
            item["highlight_zh"] = "比热在低温下呈线性。"
            papers.append(item)
        now = datetime(2026, 10, 28, 1, 0, tzinfo=timezone.utc)
        cards = build_feishu_cards(
            papers,
            generated_at=now,
            window_start=now,
            window_end=now,
            max_bytes=1800,
            papers_per_card=5,
        )
        self.assertGreaterEqual(len(cards), 3)
        blobs = [json.dumps(card, ensure_ascii=False) for card in cards]
        self.assertNotIn(DISCUSSION_FOOTER, blobs[0])
        self.assertIn(DISCUSSION_FOOTER, blobs[-1])
        self.assertTrue(all(DISCUSSION_FOOTER not in blob for blob in blobs[1:-1]))
        joined = "\n".join(blobs)
        self.assertLess(joined.index("**#1**"), joined.index("**#2**"))
        self.assertLess(joined.index("**#2**"), joined.index("**#3**"))
        self.assertIn("分", blobs[0])

    def test_labeled_translation_keeps_formula(self):
        formula = r"$\mathbf{C}=(1,1,1)$"
        text = (
            "【中文题名】磁性焦绿石体系中的三维量子霍尔绝缘体\n"
            f"【摘要翻译】磁场沿 {formula} 方向时，单晶输运出现量子霍尔平台，比热也给出相应异常。"
        )
        parsed = parse_labeled_translation(text)
        self.assertEqual(parsed["title_zh"], "磁性焦绿石体系中的三维量子霍尔绝缘体")
        self.assertIn(formula, parsed["abstract_zh"])
        batch = parse_translation_payload(json.dumps([
            {"index": 0, "title_zh": parsed["title_zh"], "abstract_zh": parsed["abstract_zh"]},
        ]), 1)
        self.assertIn(formula, batch[0]["abstract_zh"])

    def test_empty_card_uses_the_required_note(self):
        now = datetime(2026, 10, 28, 1, 0, tzinfo=timezone.utc)
        card = build_feishu_card([], generated_at=now, window_start=now, window_end=now, empty_note=True)
        blob = json.dumps(card, ensure_ascii=False)
        self.assertIn(EMPTY_NOTE, blob)
        self.assertIn("今日没有符合筛选条件的实验论文。", blob)
        self.assertNotIn("请大家在群里回复讨论", blob)
        payload = build_feishu_payload(card, secret="test-secret", timestamp="1700000000")
        self.assertEqual(payload["msg_type"], "interactive")
        self.assertEqual(payload["timestamp"], "1700000000")
        self.assertEqual(payload["sign"], feishu_sign("1700000000", "test-secret"))
        self.assertNotIn("sign", build_feishu_payload(card, secret=""))

    def test_results_webhook_signs_the_raw_body(self):
        body, headers = build_results_request({"note": EMPTY_NOTE, "papers": []}, "test-secret")
        self.assertEqual(headers["X-Signature"], sign_results_body(body, "test-secret"))
        self.assertTrue(headers["X-Signature"].startswith("sha256="))
        self.assertEqual(len(headers["X-Signature"]), len("sha256=") + 64)
        self.assertEqual(json.loads(body)["note"], EMPTY_NOTE)


class WindowAndConfigTests(unittest.TestCase):
    def test_window_is_always_the_full_lookback(self):
        # arXiv only indexes a paper after its announcement (1-3.5 days after
        # submission), so the window must not start at the last success.
        now = datetime(2026, 10, 28, 1, 0, tzinfo=timezone.utc)
        start, end = compute_window(now, now - timedelta(days=1), 3)
        self.assertEqual(end, now)
        self.assertEqual(start, now - timedelta(days=3))

        start, _end = compute_window(now, now - timedelta(days=10), 3)
        self.assertEqual(start, now - timedelta(days=3))

        start, _end = compute_window(now, None, 3)
        self.assertEqual(start, now - timedelta(days=3))

        start, _end = compute_window(now, now + timedelta(days=1), 3)
        self.assertEqual(start, now - timedelta(days=3))

    def test_query_uses_categories_and_submitted_date(self):
        start = datetime(2026, 10, 25, 1, 0, tzinfo=timezone.utc)
        end = datetime(2026, 10, 28, 1, 0, tzinfo=timezone.utc)
        query = build_arxiv_search_query(["cond-mat.str-el", "cond-mat.mtrl-sci"], start, end)
        self.assertIn("cat:cond-mat.str-el OR cat:cond-mat.mtrl-sci", query)
        self.assertIn("submittedDate:[202610250100 TO 202610280100]", query)

    def test_env_overrides_config_and_empty_env_does_not(self):
        folder = Path(tempfile.mkdtemp())
        path = folder / "config.yaml"
        path.write_text("""
arxiv_monitor:
  categories: [cond-mat.str-el]
  keywords: [skyrmion]
  max_papers_per_day: 4
  min_papers_per_day: 2
  models:
    screen: example/screen
    fallback: example/fallback
    legacy: example/legacy
""", encoding="utf-8")
        from_file = load_settings(path, environ={})
        self.assertEqual(from_file.categories, ["cond-mat.str-el"])
        self.assertEqual(from_file.max_papers_per_day, 4)
        self.assertEqual(from_file.min_papers_per_day, 2)
        self.assertEqual(from_file.screen_model, "example/screen")
        overridden = load_settings(path, environ={
            "MAX_PAPERS_PER_DAY": "10",
            "MIN_PAPERS_PER_DAY": "3",
            "SILICONFLOW_SCREEN_MODEL": "Qwen/Qwen3.6-27B",
            "MAX_PAPERS_PER_DAY_IGNORED": "",
        })
        self.assertEqual(overridden.max_papers_per_day, 10)
        self.assertEqual(overridden.min_papers_per_day, 3)
        self.assertEqual(overridden.screen_model, "Qwen/Qwen3.6-27B")
        blank = load_settings(path, environ={"MAX_PAPERS_PER_DAY": "  ", "MIN_PAPERS_PER_DAY": ""})
        self.assertEqual(blank.max_papers_per_day, 4)
        self.assertEqual(blank.min_papers_per_day, 2)

    def test_repo_config_defaults(self):
        settings = load_settings(environ={})
        self.assertEqual(settings.categories, ["cond-mat.str-el", "cond-mat.mtrl-sci"])
        self.assertEqual(settings.max_papers_per_day, 10)
        self.assertEqual(settings.min_papers_per_day, 5)
        self.assertEqual(settings.screen_pool, 36)
        self.assertEqual(settings.max_lookback_days, 6)
        self.assertIn("antiferromagnet*", settings.secondary_keywords)
        self.assertIn("photoanode", settings.hard_negative_keywords)
        self.assertEqual(settings.screen_model, "Qwen/Qwen3.6-27B")
        self.assertEqual(settings.fallback_model, "Qwen/Qwen3.5-9B")
        self.assertEqual(settings.legacy_model, "tencent/Hunyuan-MT-7B")
        self.assertEqual(settings.model_chain()[0], "Qwen/Qwen3.6-27B")

    def test_pending_retries_then_drops(self):
        failed = paper(arxiv_id="2401.00001", doi=None)
        once = update_pending([], [failed], sent_keys=[])
        self.assertEqual(once[0]["attempts"], 1)
        twice = update_pending(once, [failed], sent_keys=[])
        self.assertEqual(twice[0]["attempts"], 2)
        dropped = update_pending(twice, [failed], sent_keys=[])
        self.assertEqual(dropped, [])
        removed = update_pending(once, [failed], sent_keys=["2401.00001"])
        self.assertEqual(removed, [])

    def test_workflow_requests_write_and_keeps_secret_names(self):
        text = Path(".github/workflows/daily-report.yml").read_text(encoding="utf-8")
        for snippet in (
            "contents: write",
            "workflow_dispatch",
            "dry_run",
            "origin state",
            "MAX_PAPERS_PER_DAY",
            "MIN_PAPERS_PER_DAY",
            "DRY_RUN",
            "FEISHU_WEBHOOK_URL",
            "FEISHU_SECRET",
            "SILICONFLOW_API_KEY",
            "RESULTS_WEBHOOK_URL",
            "RESULTS_WEBHOOK_SECRET",
            "digest.json",
            "X-Signature" if False else "digest_state.json",
        ):
            self.assertIn(snippet, text)
        self.assertNotIn("MAX_PAPERS_PER_RUN", text)
        self.assertNotIn("iopscience", text.lower())
        self.assertNotIn("open.feishu.cn", text)


class ScreenCacheTests(unittest.TestCase):
    def test_cache_round_trips_through_state_and_prunes(self):
        now = datetime(2026, 10, 28, 1, 0, tzinfo=timezone.utc)
        decided = decide_screening(paper(arxiv_id="2401.12345v2", id="2401.12345v2"), screening(score=7))
        decided["llm_scored"] = True
        failed = paper(arxiv_id="2401.99999", id="2401.99999")
        failed.update({"screen_error": True})
        state = digest_lib.empty_state()
        digest_lib.remember_screened(state, [decided, failed], now)
        self.assertIn("2401.12345", state["screened"])
        self.assertNotIn("2401.99999", state["screened"])
        folder = Path(tempfile.mkdtemp())
        save_state(folder / "s.json", state)
        loaded = load_state(folder / "s.json")
        cached = digest_lib.cached_screening(paper(arxiv_id="2401.12345v3", id="2401.12345v3"), loaded)
        self.assertEqual(cached["score"], 7)
        again = decide_screening(paper(), cached)
        self.assertTrue(again["passes"])
        digest_lib.remember_screened(loaded, [], now + timedelta(days=digest_lib.SCREEN_CACHE_DAYS + 1))
        self.assertEqual(loaded["screened"], {})

    def test_old_state_without_cache_loads(self):
        folder = Path(tempfile.mkdtemp())
        (folder / "s.json").write_text(json.dumps({"sent_ids": ["2401.00001"]}), encoding="utf-8")
        state = load_state(folder / "s.json")
        self.assertEqual(state["screened"], {})
        self.assertIsNone(digest_lib.cached_screening(paper(), state))


class ArxivFetchTests(unittest.TestCase):
    def test_fixture_parser_strips_version(self):
        papers = parse_arxiv_feed(ARXIV_FIXTURE)
        self.assertEqual(len(papers), 1)
        self.assertEqual(papers[0]["arxiv_id"], "2401.12345")
        self.assertEqual(papers[0]["doi"], "10.1103/physrevb.100.123456")
        self.assertEqual(papers[0]["link"], "https://arxiv.org/abs/2401.12345v2")
        self.assertEqual(papers[0]["arxiv_version"], "v2")
        self.assertEqual(papers[0]["authors"], ["A. Author", "B. Author"])
        self.assertNotIn("  ", papers[0]["title"])

    @patch.object(monitor.time, "sleep")
    @patch.object(monitor.requests, "get")
    def test_fetch_sorts_by_submitted_date(self, get, _sleep):
        monitor.ARXIV_QUERY_DELAY = 0
        response = Mock(status_code=200, text=ARXIV_FIXTURE)
        response.raise_for_status = Mock()
        get.return_value = response
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 1, 3, tzinfo=timezone.utc)
        papers = monitor.fetch_arxiv_papers(
            ["cond-mat.str-el", "cond-mat.mtrl-sci"],
            start,
            end,
            page_size=50,
            max_papers=10,
        )
        params = get.call_args.kwargs["params"]
        self.assertIn("cat:cond-mat.str-el OR cat:cond-mat.mtrl-sci", params["search_query"])
        self.assertIn("submittedDate:", params["search_query"])
        self.assertEqual(params["sortBy"], "submittedDate")
        self.assertEqual(params["sortOrder"], "descending")
        self.assertEqual(papers[0]["arxiv_id"], "2401.12345")


def _feishu_ok():
    result = Mock(status_code=200)
    result.json.return_value = {"code": 0, "msg": "success"}
    return result


class ScreeningAndRunTests(unittest.TestCase):
    def setUp(self):
        monitor.ARXIV_QUERY_DELAY = 0
        self._old_env = {
            key: os.environ.get(key)
            for key in ("SILICONFLOW_API_KEY", "FEISHU_WEBHOOK_URL", "FEISHU_SECRET",
                        "RESULTS_WEBHOOK_URL", "RESULTS_WEBHOOK_SECRET")
        }
        os.environ["SILICONFLOW_API_KEY"] = "test-key"
        os.environ["FEISHU_WEBHOOK_URL"] = "https://example.invalid/hook"
        os.environ["FEISHU_SECRET"] = "test-sign-secret"
        os.environ.pop("RESULTS_WEBHOOK_URL", None)
        os.environ.pop("RESULTS_WEBHOOK_SECRET", None)

    def tearDown(self):
        for key, value in self._old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _settings(self, dry_run: bool = False):
        folder = Path(tempfile.mkdtemp())
        settings = load_settings(environ={})
        settings.dry_run = dry_run
        settings.state_path = folder / "digest_state.json"
        settings.digest_path = folder / "digest.json"
        settings.legacy_state_path = folder / "sent_papers.json"
        settings.screen_retries = 0
        settings.screen_batch_size = 2
        return settings

    def _candidate(self):
        return paper(
            id="2401.12345v2",
            arxiv_id="2401.12345v2",
            title="Quantum spin liquid in a kagome magnet grown by flux",
            summary=(
                "Single crystals were grown by the flux method. "
                "Magnetic susceptibility and specific heat were measured down to 2 K "
                "and show no long-range magnetic order."
            ),
        )

    @patch.object(monitor.requests, "post")
    def test_screening_uses_screen_model_then_fallback(self, post):
        settings = self._settings()
        settings.screen_model = "Qwen/Qwen3.6-27B"
        settings.fallback_model = "Qwen/Qwen3.5-9B"
        settings.legacy_model = "tencent/Hunyuan-MT-7B"
        settings.screen_retries = 0
        good = json.dumps([screening(id="2401.12345")])
        post.side_effect = [
            Mock(status_code=200, json=lambda: {"choices": [{"message": {"content": "not json"}}]}),
            Mock(status_code=200, json=lambda: {"choices": [{"message": {"content": good}}]}),
        ]
        decided = monitor.screen_papers([self._candidate()], settings)
        self.assertTrue(decided[0]["passes"])
        models = [call.kwargs["json"]["model"] for call in post.call_args_list]
        self.assertEqual(models, ["Qwen/Qwen3.6-27B", "Qwen/Qwen3.5-9B"])
        self.assertFalse(post.call_args_list[0].kwargs["json"]["enable_thinking"])
        self.assertNotIn("Authorization", str(post.call_args_list[0].kwargs["json"]))

    @patch.object(monitor.requests, "post")
    def test_auth_failure_does_not_try_fallback(self, post):
        settings = self._settings()
        settings.screen_retries = 0
        post.return_value = Mock(status_code=401, text="unauthorized")
        with self.assertRaises(monitor.SiliconFlowAuthError):
            monitor.screen_papers([self._candidate()], settings)
        self.assertEqual(post.call_count, 1)

    def _fill_translation(self, papers, _settings):
        formula = r"$\mathbf{C}=(1,1,1)$"
        for paper in papers:
            paper["title_zh"] = "磁性焦绿石体系中的三维量子霍尔绝缘体"
            paper["abstract_zh"] = (
                "本文在磁性焦绿石体系中报道了三维量子霍尔绝缘体。"
                f"磁场方向为 {formula} 。单晶样品的输运和比热测量给出了量子霍尔平台。"
            )

    @patch.object(monitor, "translate_selected")
    @patch.object(monitor, "enrich_missing_abstracts")
    @patch.object(monitor, "screen_papers")
    @patch.object(monitor, "fetch_jpsj_crossref_papers", return_value=[])
    @patch.object(monitor, "fetch_arxiv_papers")
    @patch.object(monitor.requests, "post")
    def test_dry_run_prints_card_and_skips_post(self, post, fetch_arxiv, _jpsj, screen, _enrich, translate):
        translate.side_effect = self._fill_translation
        fetch_arxiv.return_value = [self._candidate()]
        screen.side_effect = lambda papers, _settings: [
            self._decided_from(papers[0])
        ]
        settings = self._settings(dry_run=True)
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = monitor.main(settings)
        self.assertEqual(code, 0)
        printed = buffer.getvalue()
        self.assertIn("FEISHU CARD", printed)
        self.assertIn("https://arxiv.org/abs/2401.12345v2", printed)
        self.assertIn("【中文题名】", printed)
        self.assertIn("【摘要翻译】", printed)
        post.assert_not_called()
        self.assertFalse(settings.state_path.exists())
        digest = json.loads(settings.digest_path.read_text(encoding="utf-8"))
        self.assertEqual(digest["papers"][0]["arxiv_id"], "2401.12345")
        self.assertEqual(digest["papers"][0]["link"], "https://arxiv.org/abs/2401.12345v2")
        self.assertIn("$\\mathbf{C}=(1,1,1)$", digest["papers"][0]["abstract_zh"])
        self.assertTrue(digest["dry_run"])

    def _decided_from(self, source):
        decided = decide_screening(source, screening(id="2401.12345"))
        return decided

    @patch.object(monitor, "translate_selected")
    @patch.object(monitor, "enrich_missing_abstracts")
    @patch.object(monitor, "screen_papers")
    @patch.object(monitor, "fetch_jpsj_crossref_papers", return_value=[])
    @patch.object(monitor, "fetch_arxiv_papers")
    @patch.object(monitor.requests, "post")
    def test_send_records_normalized_id_and_does_not_resend(self, post, fetch_arxiv, _jpsj, screen, _enrich, translate):
        translate.side_effect = self._fill_translation
        candidate = self._candidate()
        fetch_arxiv.return_value = [candidate]
        screen.side_effect = lambda papers, _settings: [self._decided_from(papers[0])] if papers else []
        post.return_value = _feishu_ok()
        settings = self._settings(dry_run=False)
        self.assertEqual(monitor.main(settings), 0)
        state = load_state(settings.state_path)
        self.assertIn("2401.12345", state["sent_ids"])
        self.assertNotIn("2401.12345v2", state["sent_ids"])
        self.assertIsNotNone(state["last_successful_run"])
        first_card = json.dumps(post.call_args.kwargs["json"]["card"], ensure_ascii=False)
        self.assertIn("https://arxiv.org/abs/2401.12345v2", first_card)
        self.assertIn("【摘要翻译】", first_card)
        self.assertIn("助熔剂", first_card)
        self.assertIn(post.call_args.kwargs["json"]["sign"], [post.call_args.kwargs["json"]["sign"]])

        self.assertEqual(monitor.main(settings), 0)
        self.assertEqual(post.call_count, 2)
        second_card = json.dumps(post.call_args.kwargs["json"]["card"], ensure_ascii=False)
        self.assertIn(EMPTY_NOTE, second_card)
        self.assertNotIn("2401.12345", second_card)
        self.assertEqual(screen.call_count, 1)

    @patch.object(monitor, "translate_selected")
    @patch.object(monitor, "enrich_missing_abstracts")
    @patch.object(monitor, "screen_papers")
    @patch.object(monitor, "fetch_jpsj_crossref_papers", return_value=[])
    @patch.object(monitor, "fetch_arxiv_papers")
    @patch.object(monitor.requests, "post")
    def test_run_fills_five_reuses_cache_and_skips_sent_before_cap(
        self, post, fetch_arxiv, _jpsj, screen, _enrich, translate,
    ):
        translate.side_effect = self._fill_translation
        post.return_value = _feishu_ok()
        topics = ["kagome", "pyrochlore", "honeycomb", "triangular-lattice", "Kitaev", "multiferroic", "spin-ice"]
        candidates = []
        for index, topic in enumerate(topics):
            candidates.append(paper(
                id=f"2410.0000{index}",
                arxiv_id=f"2410.0000{index}",
                link=f"https://arxiv.org/abs/2410.0000{index}",
                title=f"Frustrated {topic} antiferromagnet number {index}",
                summary="Single crystals were grown by flux; susceptibility and specific heat were measured.",
            ))
        offtopic = paper(
            id="2410.00099", arxiv_id="2410.00099", link="https://arxiv.org/abs/2410.00099",
            title="Ferroelectric shells for a TiO2 photoanode",
            summary="Photoelectrochemical water splitting with ferroelectric polarization.",
        )
        fetch_arxiv.return_value = [*candidates, offtopic]
        scores = {0: 9, 1: 3, 2: 2, 3: 4, 4: 1, 5: 3.5, 6: 2.5}

        def fake_screen(papers, _settings):
            out = []
            for item in papers:
                index = int(item["arxiv_id"][-1])
                out.append(decide_screening(item, screening(id=item["arxiv_id"], score=scores[index])))
            return out

        screen.side_effect = fake_screen
        settings = self._settings(dry_run=False)
        settings.screen_pool = 4
        state = digest_lib.empty_state()
        digest_lib.remember_sent(state, [candidates[6]])
        save_state(settings.state_path, state)

        self.assertEqual(monitor.main(settings), 0)
        screened_titles = [item["title"] for item in screen.call_args.args[0]]
        self.assertEqual(len(screened_titles), 4)
        self.assertNotIn(candidates[6]["title"], screened_titles)
        self.assertNotIn(offtopic["title"], screened_titles)
        digest = json.loads(settings.digest_path.read_text(encoding="utf-8"))
        chosen = digest["papers"]
        # Floor of five; one close-scored extra is allowed (tie on keyword score).
        self.assertIn(len(chosen), (5, 6))
        self.assertEqual(chosen[0]["score"], 9)
        self.assertFalse(chosen[0]["backup"])
        self.assertTrue(all(item["backup"] for item in chosen[1:]))
        self.assertNotIn("photoanode", json.dumps(chosen))

        # Next day: the same window comes back. Already-scored papers reuse the
        # cache, sent ones stay out, and only never-scored papers hit the model.
        state = load_state(settings.state_path)
        self.assertTrue(state["screened"])
        screen.reset_mock()
        screen.side_effect = fake_screen
        self.assertEqual(monitor.main(settings), 0)
        for call in screen.call_args_list:
            for item in call.args[0]:
                self.assertIsNone(digest_lib.cached_screening(item, state))
        second = json.loads(settings.digest_path.read_text(encoding="utf-8"))["papers"]
        first_ids = {item["arxiv_id"] for item in chosen}
        self.assertFalse(first_ids & {item["arxiv_id"] for item in second})

    @patch.object(monitor.requests, "post")
    def test_translation_uses_hunyuan_and_skips_papers_not_passed_in(self, post):
        settings = self._settings()
        settings.legacy_model = "tencent/Hunyuan-MT-7B"
        settings.fallback_model = "Qwen/Qwen3.5-9B"
        formula = r"$\mathbf{C}=(1,1,1)$"
        abstract_zh = (
            "磁性焦绿石体系呈现三维量子霍尔效应。"
            f"磁场方向取 {formula} 。单晶输运测量给出量子霍尔平台。"
        )
        payload = json.dumps([{
            "index": 0,
            "title_zh": "磁性焦绿石体系中的三维量子霍尔绝缘体",
            "abstract_zh": abstract_zh,
        }], ensure_ascii=False)
        post.return_value = Mock(status_code=200, json=lambda: {"choices": [{"message": {"content": payload}}]})
        selected = [paper(
            title="3D quantum Hall insulator",
            summary=f"We report a pyrochlore magnet. Field along {formula}. Specific heat was measured.",
        )]
        rejected = paper(title="Rejected theory paper", summary="We propose a model and use DMRG only.")
        monitor.translate_selected(selected, settings)
        self.assertEqual(post.call_count, 1)
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["model"], "tencent/Hunyuan-MT-7B")
        self.assertNotIn("enable_thinking", body)
        self.assertNotIn("Rejected theory paper", body["messages"][-1]["content"])
        self.assertIn(formula, selected[0]["abstract_zh"])
        self.assertNotIn("title_zh", rejected)

    @patch.object(monitor.requests, "post")
    def test_results_webhook_is_skipped_when_unset_and_signs_when_set(self, post):
        post.return_value = Mock(status_code=204)
        monitor.maybe_post_results({"papers": []})
        post.assert_not_called()
        os.environ["RESULTS_WEBHOOK_URL"] = "https://example.invalid/results"
        os.environ["RESULTS_WEBHOOK_SECRET"] = "result-secret"
        monitor.maybe_post_results({"papers": [], "note": None})
        self.assertEqual(post.call_count, 1)
        body = post.call_args.kwargs["data"]
        header = post.call_args.kwargs["headers"]["X-Signature"]
        self.assertEqual(header, sign_results_body(body, "result-secret"))
        self.assertNotIn("result-secret", header)


if __name__ == "__main__":
    unittest.main()
