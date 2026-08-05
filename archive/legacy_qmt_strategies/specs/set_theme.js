const { Level } = require('level');
const path = require('path');

const dbPath = path.join(
  'C:/Users/Administrator/AppData/Roaming/Hermes/Local Storage/leveldb'
);

async function main() {
  const db = new Level(dbPath, { valueEncoding: 'utf8', keyEncoding: 'utf8' });
  
  try {
    // Read current value
    const currentVal = await db.get('hermes-desktop-theme-v2');
    console.log('Current theme:', JSON.stringify(currentVal));
  } catch (e) {
    console.log('Current theme: not found (default)');
  }
  
  // Write Midnight theme
  await db.put('hermes-desktop-theme-v2', JSON.stringify('midnight'));
  console.log('✓ Theme set to: midnight');
  
  // Verify
  const verify = await db.get('hermes-desktop-theme-v2');
  console.log('✓ Verified:', JSON.stringify(verify));
  
  await db.close();
}

main().catch(err => {
  console.error('Error:', err.message);
  process.exit(1);
});
