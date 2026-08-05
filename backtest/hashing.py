# coding: utf-8
import hashlib

def _sha(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def compute_universe_hash(codes):
    return _sha("|".join(sorted(codes)))

def compute_data_hash(db_path, db_mtime, adjustment,
                     requested_start, requested_end,
                     actual_min, actual_max,
                     n_codes, n_rows_after_dedup, dedup_count,
                     universe_hash):
    parts = [str(db_path), str(db_mtime), str(adjustment),
             str(requested_start), str(requested_end),
             str(actual_min), str(actual_max),
             str(n_codes), str(n_rows_after_dedup), str(dedup_count),
             str(universe_hash)]
    return _sha("|".join(parts))

def compute_config_hash(yaml_text):
    return _sha(yaml_text)
