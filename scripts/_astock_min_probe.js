const fs = require('fs');
const path = require('path');

const D = 'D:\\astock';

function dirSummary(d) {
  const files = fs.readdirSync(d).filter((f) => f.endsWith('.parquet'));
  return files.length;
}

for (const d of [
  'min_1min',
  'min_5min',
  'min_15min',
  'min_30min',
  'min_60min',
]) {
  const p = path.join(D, d);
  const n = dirSummary(p);
  // 取第一个文件看修改时间
  let newest = 0;
  let oldest = Infinity;
  for (const f of fs.readdirSync(p)) {
    const st = fs.statSync(path.join(p, f));
    if (st.mtimeMs > newest) newest = st.mtimeMs;
    if (st.mtimeMs < oldest) oldest = st.mtimeMs;
  }
  console.log(
    d.padEnd(12, ' ') +
      ' files=' +
      String(n).padStart(5, ' ') +
      '  oldest=' +
      new Date(oldest).toISOString().slice(0, 10) +
      '  newest=' +
      new Date(newest).toISOString().slice(0, 10)
  );
}

console.log('');
for (const f of [
  'daily\\stock_daily.parquet',
  'basic\\stock_basic.parquet',
]) {
  const p = path.join(D, f);
  if (fs.existsSync(p)) {
    const st = fs.statSync(p);
    console.log(
      f.padEnd(30, ' ') +
        ' size=' +
        String(st.size).padStart(12, ' ') +
        '  mtime=' +
        new Date(st.mtimeMs).toISOString().slice(0, 10)
    );
  } else {
    console.log(f.padEnd(30, ' ') + ' MISSING');
  }
}