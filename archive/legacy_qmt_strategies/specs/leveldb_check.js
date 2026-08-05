const fs = require('fs');
const path = require('path');

const dbPath = 'C:/Users/Administrator/AppData/Roaming/Hermes/Local Storage/leveldb';
const logFile = path.join(dbPath, '000003.log');

const data = fs.readFileSync(logFile);
const text = data.toString('latin1');

// LevelDB batch format: look for key-value pairs
// Keys in leveldb are preceded by length bytes
for (let i = 0; i < data.length; i++) {
  // Try to find our theme key
  const needle = Buffer.from('hermes-desktop-theme-v2');
  if (data[i] === needle[0]) {
    const slice = data.slice(i, i + 200);
    const decoded = slice.toString('latin1');
    // Find the actual value boundaries
    const clean = decoded.replace(/[\x00-\x08\x0e-\x1f]/g, '·');
    if (clean.includes('hermes-desktop-theme')) {
      console.log(`Found at offset ${i}:`);
      console.log(clean.substring(0, 120));
    }
  }
  
  // Also search for the value "midnight" or "nous"
  if (data[i] === 0x6e) { // 'n'
    const possible = data.slice(i, i + 20).toString('latin1');
    if (possible.startsWith('nous') || possible.startsWith('midnight') || possible.startsWith('slate')) {
      console.log(`Value candidate at offset ${i}: ${possible.substring(0, 30)}`);
    }
  }
}
