"""Explicit numeric/native contracts shared by CatBoost training and serving.

Native categories are an opt-in diagnostic, outside the frozen numeric matrix.
Integer codes are never rounded, truncated or filled with a real category.
"""
from __future__ import annotations

import numpy as np

NATIVE_CATEGORIES = ("station_code", "hour", "weekday", "month", "weekend", "horizon")


def category_indices(columns, categorical_columns):
    if len(set(columns)) != len(columns) or len(set(categorical_columns)) != len(categorical_columns):
        raise ValueError("Duplicate feature/category names")
    if any(name not in columns for name in categorical_columns):
        raise ValueError("Categorical feature outside schema")
    return [columns.index(name) for name in categorical_columns]


def checked_matrix(matrix, columns, categorical_columns):
    array = np.asarray(matrix)
    if array.ndim != 2 or array.shape[1] != len(columns):
        raise ValueError("CatBoost feature matrix shape mismatch")
    for index in category_indices(columns, categorical_columns):
        values = array[:, index]
        if not np.isfinite(values).all() or np.any(np.abs(values) > 2**53 - 1) or np.any(values != np.floor(values)):
            raise ValueError("Categorical codes must be finite exact integers")
    return array


def prediction_input(matrix, columns, categorical_columns):
    array = checked_matrix(matrix, columns, categorical_columns)
    if not categorical_columns:
        return array
    import pandas as pd
    from catboost import Pool
    frame = pd.DataFrame(array, columns=columns)
    for name in categorical_columns:
        frame[name] = frame[name].astype("int64")
    return Pool(frame, cat_features=category_indices(columns, categorical_columns), feature_names=columns, thread_count=4)


def validate_model_contract(model, columns, categorical_columns):
    expected = sorted(category_indices(columns, categorical_columns))
    if model.get_cat_feature_indices() != expected:
        raise ValueError("CatBoost categorical model/manifest mismatch")
    if list(model.feature_names_) != list(columns):
        raise ValueError("CatBoost feature names/order mismatch")


def write_rows(rows, tsv, cd, categorical_columns=()):
    """Bounded batches; every eligible row, in original order, including NaNs."""
    indices = set(category_indices(rows.columns, categorical_columns))
    formats = ["%d", *["%d" if i in indices else "%.9g" for i in range(len(rows.columns))]]
    partial = tsv.with_name(tsv.name + ".partial")
    written = 0
    with partial.open("w", encoding="utf-8", newline="") as stream:
        for first in range(0, len(rows), 32768):
            selection = slice(first, min(len(rows), first + 32768))
            x = checked_matrix(rows[selection], rows.columns, categorical_columns)
            y = rows.labels(selection)
            if len(y) != len(x) or not np.isin(y, (0, 1)).all():
                raise ValueError("Invalid binary labels or row alignment")
            np.savetxt(stream, np.column_stack((y, x)), delimiter="\t", fmt=formats)
            written += len(x)
    if written != len(rows):
        raise ValueError("CatBoost TSV row count mismatch")
    partial.replace(tsv)
    cd.write_text("0\tLabel\n" + "".join(
        f"{i + 1}\t{'Categ' if i in indices else 'Num'}\t{name}\n"
        for i, name in enumerate(rows.columns)), encoding="utf-8")
    return written


def native_diagnostic_pool(rows, directory, *, borders=None):
    """Raw file Pool: native categories cannot use utils.quantize/file cache.

    This does NOT make CatBoost out-of-core. Full row memory must be admitted
    separately. No research queue registers this diagnostic as a frozen fit.
    """
    from catboost import Pool
    from .common import digest, save_json
    directory.mkdir(parents=True, exist_ok=False)
    categories = [name for name in NATIVE_CATEGORIES if name in rows.columns]
    tsv, cd = directory / "rows.tsv", directory / "columns.cd"
    count = write_rows(rows, tsv, cd, categories)
    pool = Pool(str(tsv), column_description=str(cd), thread_count=4)
    if pool.num_row() != count or pool.get_cat_feature_indices() != sorted(category_indices(rows.columns, categories)):
        raise ValueError("Native file Pool contract mismatch")
    options = {"border_count": 64, "random_seed": 20261026, "nan_mode": "Min"}
    if borders is not None:
        options["input_borders"] = str(borders)
    pool.quantize(**options)
    save_json(directory / "contract.json", {"rows": count, "features": rows.columns,
        "categorical_columns": categories, "all_valid_rows_used": True,
        "storage": "raw_file_pool_then_in_memory_quantization_no_serialized_categorical_pool",
        "tsv_sha256": digest(tsv), "cd_sha256": digest(cd),
        "borders_sha256": digest(borders) if borders is not None else None,
        "purpose": "diagnostic_not_in_frozen_64", "out_of_core_training": False})
    return pool
