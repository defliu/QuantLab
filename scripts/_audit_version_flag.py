# -*- coding: utf-8 -*-
"""验证 D/E 侧重复组是否为「update_flag 版本对」，据此定「同侧去重」裁决规则。

去重审计发现：E 侧几乎所有撞键组都判为 DIFFERS，且首现差异列高度集中在
update_flag（balancesheet 103554/103564、forecast 21196/211196）。
本脚本把每个撞键组按「flag 是否有别」×「业务载荷(除 update_flag)是否有别」
交叉分类，回答 4 个决定 R2(update_flag=max) 能否自动裁决的问题：

  A  同 flag 且载荷相同      -> 纯重复行，安全丢弃，规则无歧义
  B  同 flag 但载荷不同      -> flagmax 无法定位版本，R2 失效（必须人工裁决）
  C  flag 有别且载荷相同     -> 同一业务记录的版本对，flagmax 唯一且安全
  D  flag 有别且载荷不同     -> 需查：最大 flag 行是否唯一(D-1)、是否比
                              低 flag 行更完整(D-2)、改的是哪些字段(D-3)
  E  组内 flag 全缺/无法定序 -> 无法用 flag 裁决

同时量化 R1(物理最后一行) 与 R2(flag 最大) 的分歧程度：若两者大面积不一致，
则「按物理序覆盖」这条省事规则等价于随机取版本，必须弃用。

实现要点：全向量化，零逐组 Python 循环（fina_indicator E 侧 29.8 万组）。
逐列相等判定复用 _colcmp.col_diff_flags（缺失值/日期/数值兜底语义有单测保证），
组编码复用 _audit_dedup_safety._diff_matrix。
只读，不写盘。
"""
import os
import sys
from collections import Counter

import numpy as np
import pandas as pd

sys.path.insert(
    0,
    os.path.dirname(os.path.abspath(__file__))
)
from _audit_dedup_safety import (  # noqa: E402
    D_BASE,
    E_ROOT,
    FIN_TABLES,
    _diff_matrix,
    _read,
    key_strings,
)
from _colcmp import NA, SEP, is_date_col  # noqa: E402
from _keynorm import isna_scalar  # noqa: E402

MAX_SHOW = 3
RICH_CAP = 300000


def _flag_array(s):
    """update_flag -> 归一化字符串数组（缺失 -> NA）。

    只做「缺失 -> NA + 去空白」，不做数值归一化；
    数值归一化交给 _flag_order_key，避免同一处逻辑两套实现。
    """
    return s.map(
        lambda v: NA if isna_scalar(v) else str(v).strip()
    ).to_numpy(dtype=object)


def _flag_order_key(flag):
    """update_flag -> (数值排序键, 规范字符串, 无法解析数值的个数)。

    为什么必须数值归一化（2026-10-01 实测）：
      fina_indicator 的 update_flag 是 str dtype，且同一列里混着
      '0' / '0.0' / '1' / '1.0' 四种写法。若按原始字符串 factorize，
      '1' 与 '1.0' 会被当成「两个不同的 flag」，凭空造出 567 个
      「flag 有别」的假版本对；转成数值后它们又同为 1.0，导致
      flagmax 把两行都判成最大版本 -> 假「最大 flag 不唯一」。
      所以「区分 flag 是否有别」和「比较 flag 大小」必须用同一套
      规范字符串，否则分类与裁决会自相矛盾。

    无法解析为数字的值（既非缺失、也非数字串）退回字符串序，
    但必须显式报数——这种值不能被当成业务版本顺序使用。
    """
    s = pd.Series(flag, dtype=object)
    na_mask = np.array([x == NA for x in flag], dtype=bool)
    num = pd.to_numeric(s, errors='coerce')
    n_unparsed = int((num.isna().to_numpy() & ~na_mask).sum())
    key = num.to_numpy(dtype='float64')
    canon = np.where(
        na_mask,
        NA,
        np.where(
            np.isnan(key),
            np.array(flag, dtype=object),
            np.array(
                [str(int(v)) if float(v).is_integer() else str(v)
                 for v in key],
                dtype=object,
            ),
        ),
    )
    return key, canon, n_unparsed


def _group_stat(gcode, values, how):
    """按组聚合 values（float/object），返回按组编码升序排列的完整数组。

    注意：gcode 是 factorize 出来的稀疏编码，子集化后可能不从 0 连续
    （例如只取 12911 个组时最大编码仍是 21195）。因此必须 reindex 回
    [0, max] 全区间，否则调用方用 gcode 去索引会越界或错位。
    """
    tmp = pd.DataFrame({'g': gcode, 'v': values})
    agg = tmp.groupby('g')['v']
    out = (agg.max() if how == 'max' else agg.min())
    full = pd.Series(
        np.arange(int(gcode.max()) + 1),
        index=pd.RangeIndex(int(gcode.max()) + 1),
    )
    return out.reindex(full.index).to_numpy()


def _nonnull_count(sub, cols):
    """NA 感知非空字段数：数值列 notna；字符串列 notna 且去空白后非空。

    不用 df.notna().sum()，因为它把 '' 计入非空，会虚高完整度。
    """
    if not cols:
        return np.zeros(len(sub), dtype='int32')
    acc = np.zeros(len(sub), dtype='int32')
    for c in cols:
        s = sub[c]
        ok = s.notna()
        if not (
            pd.api.types.is_numeric_dtype(s)
            or pd.api.types.is_datetime64_any_dtype(s)
            or pd.api.types.is_bool_dtype(s)
        ):
            blank = (
                s.astype(str).str.strip().eq('')
            )
            ok = ok & ~blank.fillna(True)
        acc += ok.to_numpy(dtype='int32')
    return acc


def _classify(name, keys, path, tag):
    if not os.path.exists(path):
        print('  %s：文件不存在，跳过' % tag, flush=True)
        return
    df = _read(path, keys)
    if 'update_flag' not in df.columns:
        print('  %s：无 update_flag 列，跳过' % tag, flush=True)
        del df
        return

    date_cols = set(k for k in keys if is_date_col(k))
    karr = key_strings(df, keys, date_cols)
    dup = pd.Index(karr).duplicated(keep=False)
    n_dup = int(dup.sum())
    if not n_dup:
        print('  %s：无撞键' % tag, flush=True)
        del df
        return

    sub = df.loc[dup].copy()
    sub['__k'] = karr[dup]
    del df
    all_date_cols = set(c for c in sub.columns if is_date_col(c))
    payload = [
        c for c in sub.columns if c not in keys and c != 'update_flag'
    ]

    flag = _flag_array(sub['update_flag'])
    flag_key, flag_canon, n_unparsed = _flag_order_key(flag)
    row_diff, base_of_row, gcode = _diff_matrix(
        sub, payload, all_date_cols
    )
    n_groups = int(gcode.max()) + 1
    grp_diff = pd.DataFrame(row_diff).groupby(gcode, sort=False).any()
    grp_diff.columns = payload
    payload_diff = grp_diff.any(axis=1).to_numpy()

    # 组内不同 flag 值的个数。用「规范字符串」factorize，与下面的
    # flagmax 比较共用同一套口径（否则 '1' vs '1.0' 会同时造成
    # 「假 flag 有别」和「假最大 flag 不唯一」）。
    fcode, _ = pd.factorize(
        pd.Series(flag_canon, dtype=object), sort=False
    )
    pair = pd.DataFrame({'g': gcode, 'f': fcode}).drop_duplicates()
    n_dist_flag = pair.groupby('g').size().sort_index().to_numpy()
    flag_varies = n_dist_flag > 1

    flag_differs = payload_diff
    same_flag_risk = int((~flag_varies & flag_differs).sum())
    same_flag_safe = int((~flag_varies & ~flag_differs).sum())
    flag_only = int((flag_varies & ~flag_differs).sum())
    both = int((flag_varies & flag_differs).sum())

    # 全部 flag 缺失的组：任何基于 flag 的裁决都无法定序，必须单独报出。
    flag_all_na = pd.DataFrame(
        {'g': gcode, 'f': flag_canon}
    ).groupby('g')['f'].apply(
        lambda s: bool((s == NA).all())
    ).to_numpy()
    n_all_na = int(flag_all_na.sum())

    rows_per_group = np.bincount(gcode)
    mx = _group_stat(gcode, flag_key, 'max')
    mn = _group_stat(gcode, flag_key, 'min')
    mx_row = mx[gcode]
    # flagmax 命中：数值等于组内最大、且该组存在可解析的非缺失 flag。
    is_max = (flag_key == mx_row) & ~pd.isna(mx_row)
    is_max = np.asarray(is_max, dtype=bool)
    n_max_rows = np.bincount(
        gcode, weights=is_max.astype('int64')
    ).astype('int64')

    print('  %s：撞键组=%d  撞键行=%d  组内行数分布=%s'
          % (tag, n_groups, n_dup,
             dict(sorted(Counter(rows_per_group.tolist()).items()))),
          flush=True)
    print('       flag 排序键=numeric  无法解析为数字的 flag 值=%d'
          '  全部 flag 缺失的组=%d' % (n_unparsed, n_all_na), flush=True)
    print('       [A 同flag载荷同]=%d  [B 同flag载荷异->flagmax失效]=%d'
          % (same_flag_safe, same_flag_risk), flush=True)
    print('       [C flag有别载荷同]=%d  [D flag有别载荷异]=%d'
          % (flag_only, both), flush=True)

    # D-1：最大 flag 行是否唯一（R2 能否唯一定位版本）
    d_bad = np.flatnonzero(flag_varies & flag_differs)
    if len(d_bad):
        n_max_bad = int((n_max_rows[d_bad] != 1).sum())
        print('       D-1 最大flag行唯一？唯一=%d  不唯一/缺失=%d'
              % (len(d_bad) - n_max_bad, n_max_bad), flush=True)
        cnt = Counter(n_max_rows[d_bad].tolist())
        print('           D组内最大flag行数分布=%s' % dict(sorted(cnt.items())), flush=True)
        if both:
            col_counts = grp_diff.loc[d_bad].sum().sort_values(ascending=False)
            col_counts = col_counts[col_counts > 0]
            print('       D-3 D组差异集中列：%s'
                  % ', '.join('%s=%d' % (c, int(n)) for c, n in col_counts.head(8).items()),
                  flush=True)

    # D-2：最大 flag 行是否比低 flag 行更完整
    both_ids = np.flatnonzero(flag_varies & flag_differs)
    if len(both_ids) and len(payload):
        rows_idx = np.flatnonzero(np.isin(gcode, both_ids))
        cap = RICH_CAP
        if len(rows_idx) > cap:
            rng = np.random.default_rng(20261001)
            keep_g = np.unique(
                gcode[rng.choice(rows_idx, size=cap, replace=False)]
            )
            rows_idx = np.flatnonzero(np.isin(gcode, keep_g))
        s2 = sub.iloc[rows_idx]
        g2 = gcode[rows_idx]
        k2, _c2, _ = _flag_order_key(flag[rows_idx])
        mx2 = _group_stat(g2, k2, 'max')[g2]
        mn2 = _group_stat(g2, k2, 'min')[g2]
        # 关键：k2 已是 float64（见 _flag_order_key），不再无条件转换——
        # 旧写法在遇到字面 '<NA>' 时会抛 could not convert string to float。
        ok2 = ~np.isnan(mx2)
        is_hi = (k2 == mx2) & ok2
        is_lo = (k2 == mn2) & ok2
        nn = _nonnull_count(s2, payload).astype('float64')
        av = np.where(is_hi, nn, np.nan)
        bv = np.where(is_lo, nn, np.nan)
        agg = pd.DataFrame({'g': g2, 'hi': av, 'lo': bv}).groupby('g').max()
        hi_w = int((agg['hi'] > agg['lo']).sum())
        lo_w = int((agg['lo'] > agg['hi']).sum())
        tie = int((agg['hi'] == agg['lo']).sum())
        tot = hi_w + lo_w + tie
        if tot:
            print('       D-2 完整度对比(样本组=%d/%d)：高flag更完整=%d(%.1f%%)  '
                  '低flag更完整=%d(%.1f%%)  持平=%d(%.1f%%)'
                  % (tot, len(both_ids), hi_w, 100.0 * hi_w / tot,
                     lo_w, 100.0 * lo_w / tot, tie, 100.0 * tie / tot),
                  flush=True)

    # D-4：差异的方向。决定 flagmax 到底是「取更新的版本」还是「取更旧的版本」。
    #   FILL_ONLY  仅高flag非空（低flag空） -> 高flag 是补充版，flagmax 正确
    #   DROP_ONLY  仅低flag非空（高flag空） -> 高flag 丢了字段，flagmax 可疑
    #   BOTH_DIFF  双方非空但值不同        -> 修正/改写，flagmax 需业务确认
    #   HI_EMPTY   双方都空                -> 格式类假差异（不应出现）
    if len(d_bad) and len(payload):
        # 找「恰好 2 行 且 最大flag行唯一」的组，做高/低版本配对。
        # 全向量化：用 scatter 建 组->高行号 / 组->低行号 的映射，
        # 不用 np.flatnonzero(gcode == gi) 逐组扫（那是 O(组数×总行数)，
        # fina_indicator E 侧 29.8 万组会跑到 10^10 量级）。
        # 3 行组、或两行同 flag 的组不满足条件，配不上是正常的，
        # 因此用掩码记录成功配对的组，最后按掩码统一对齐。
        n_all_d = len(d_bad)
        two_row = rows_per_group == 2
        ok_pair = two_row & (n_max_rows == 1)
        sel = np.flatnonzero(ok_pair[d_bad])
        n_pair = len(sel)

        if n_pair:
            gi = d_bad[sel]
            hi_of = np.full(n_groups, -1, dtype=np.int64)
            lo_of = np.full(n_groups, -1, dtype=np.int64)
            in_d = np.isin(gcode, gi)
            rows_in = np.flatnonzero(in_d)
            g_in = gcode[rows_in]
            hi_of[g_in[is_max[rows_in]]] = rows_in[is_max[rows_in]]
            lo_of[g_in[~is_max[rows_in]]] = rows_in[~is_max[rows_in]]
            hi_idx = hi_of[gi]
            lo_idx = lo_of[gi]
            d_sel = grp_diff.to_numpy()[gi]

            # 只在 2*n_pair 个配对行上做归一化，而不是整帧 n 行 x 每列一次
            # （fina_indicator E 侧整帧 59.7 万行 x 上百列的 lambda 归一化
            #   是分钟级开销，收敛到配对行后是毫秒级）。
            pos = np.concatenate([hi_idx, lo_idx])
            direction = {}
            for j, c in enumerate(payload):
                sc = sub[c].iloc[pos]
                if (
                    pd.api.types.is_numeric_dtype(sc)
                    and c not in all_date_cols
                ):
                    hv = sc.to_numpy(dtype='float64', copy=False)
                    ok = ~np.isnan(hv)
                else:
                    ok = (
                        sc.map(
                            lambda v: NA
                            if isna_scalar(v) else str(v).strip()
                        ).to_numpy(dtype=object) != NA
                    )
                h_ok = ok[:n_pair]
                l_ok = ok[n_pair:]
                both_ok = h_ok & l_ok
                d = d_sel[:, j]
                if not d.any():
                    continue
                direction[c] = (
                    int((d & h_ok & ~l_ok).sum()),
                    int((d & ~h_ok & l_ok).sum()),
                    int((d & both_ok).sum()),
                )
            if direction:
                print('       D-4 配对成功组=%d/%d；各差异列方向'
                      '（仅高flag非空 / 仅低flag非空 / 双方非空异值），'
                      '取影响组数前 6：'
                      % (n_pair, n_all_d), flush=True)
                top = sorted(
                    direction.items(),
                    key=lambda kv: -(sum(kv[1])),
                )[:6]
                for c, (f, dr, b) in top:
                    print('           %-24s %d / %d / %d' % (c, f, dr, b), flush=True)
                tot_f = sum(v[0] for v in direction.values())
                tot_d = sum(v[1] for v in direction.values())
                tot_b = sum(v[2] for v in direction.values())
                print('           合计：仅高flag非空=%d  仅低flag非空=%d  双方非空异值=%d'
                      % (tot_f, tot_d, tot_b), flush=True)

    # R1(物理最后一行) vs R2(flag 最大) 分歧
    # 判定方式：取每组物理上最靠后的那一行，看它是否正好是 is_max 行。
    # 若不是，则「按物理序覆盖」会选中低 flag 的旧版本 —— R1 与 R2 结论不同。
    last_idx = (
        pd.Series(np.arange(len(gcode))).groupby(gcode).max().sort_index().to_numpy()
    )
    for label, sel in (
        ('flag有别且载荷异', flag_varies & flag_differs),
        ('flag有别(全部)', flag_varies),
    ):
        ids = np.flatnonzero(sel)
        if not len(ids):
            continue
        hit = int(is_max[last_idx[ids]].sum())
        print('       R1 vs R2 [%s] 组=%d  物理最后一行恰为最大flag行=%d(%.1f%%)  分歧=%d'
              % (label, len(ids), hit, 100.0 * hit / len(ids), len(ids) - hit),
              flush=True)
    del sub, grp_diff, row_diff


def audit(name, keys, failures):
    print('=' * 110, flush=True)
    print('表：finance/%s  主键：%s' % (name, ' + '.join(keys)), flush=True)
    for tag, path in (
        ('D', os.path.join(D_BASE, 'finance', name + '.parquet')),
        ('E', os.path.join(E_ROOT, name + '.parquet')),
    ):
        try:
            _classify(name, keys, path, tag)
        except Exception as exc:
            # 必须记账并向上传播：旧写法只打印 traceback 就继续，
            # 末尾照样输出 DONE、退出码 0 —— 会把「跑到一半炸了」
            # 的残缺审计误当成完整成功日志（2026-10-01 fina_indicator
            # D-2 崩溃即是以此方式伪装成 DONE 的）。
            failures.append('%s/%s' % (name, tag))
            print('  !! 异常 %s %s: %r' % (name, tag, exc), flush=True)
            import traceback
            traceback.print_exc()


def main():
    only = sys.argv[1:] or None
    failures = []
    for name, keys in FIN_TABLES:
        if only and name not in only:
            continue
        try:
            audit(name, keys, failures)
        except Exception as exc:
            failures.append('%s' % name)
            print('  !! 异常 %s: %r' % (name, exc), flush=True)
            import traceback
            traceback.print_exc()
    if failures:
        # 不打印 DONE：有任何一侧失败，结论都不完整。
        print('PARTIAL_FAILED=%s' % ','.join(failures), flush=True)
        sys.exit(1)
    print('DONE', flush=True)


if __name__ == '__main__':
    main()
