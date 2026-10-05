const { execSync } = require('child_process');
const path = require('path');

try {
    const result = execSync(
        '"D:\\Python311\\python.exe" reconcile_ens.py --date 20261001',
        {
            cwd: 'D:\\QuantLab\\projects\\Project_16_LightGBM股票大师',
            encoding: 'utf8',
            maxBuffer: 50 * 1024 * 1024,
            timeout: 300000
        }
    );
    console.log('RC: 0');
    console.log(result);
} catch (e) {
    console.log('RC:', e.status);
    console.log('STDERR:', e.stderr ? e.stderr.toString() : '');
    console.log('STDOUT:', e.stdout ? e.stdout.toString() : '');
    process.exit(e.status || 1);
}