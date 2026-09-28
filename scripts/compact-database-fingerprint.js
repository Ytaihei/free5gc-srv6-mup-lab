// Executed by the SAME locked mongosh on each isolated migration stage.
// Keep BSON types and field order; sort document hashes, not the stored data.
const crypto = require('crypto');
const hash = value => crypto.createHash('sha256').update(EJSON.stringify(value, {relaxed: false})).digest('hex');
const admin = db.getSiblingDB('admin');
const opts = admin.runCommand({getCmdLineOpts: 1});
if (opts.ok !== 1 || opts.parsed.replication || opts.parsed.security) {
    throw new Error('Only unauthenticated standalone lab databases are supported');
}
const users = admin.runCommand({usersInfo: {forAllDBs: true}});
const roles = admin.runCommand({rolesInfo: 1, showBuiltinRoles: false});
if (users.ok !== 1 || roles.ok !== 1 || users.users.length || roles.roles.length) {
    throw new Error('Custom authentication requires a separately reviewed migration');
}
const databases = admin.runCommand({listDatabases: 1, nameOnly: true});
if (databases.ok !== 1) throw new Error('Cannot enumerate databases');
const result = [];
for (const entry of databases.databases.sort((a, b) => a.name.localeCompare(b.name))) {
    if (['admin', 'config', 'local'].includes(entry.name)) continue;
    const database = db.getSiblingDB(entry.name);
    for (const c of database.getCollectionInfos().sort((a, b) => a.name.localeCompare(b.name))) {
        if (c.type !== 'collection' || c.options.timeseries || c.options.capped || c.name.startsWith('system.')) {
            throw new Error('Unsupported collection kind; no automatic conversion');
        }
        const collection = database.getCollection(c.name);
        const valid = database.runCommand({validate: c.name, full: true});
        if (valid.ok !== 1 || valid.valid !== true) throw new Error('Invalid source collection');
        const indexes = collection.getIndexes().map(index => {
            // Legacy ns is redundant with database + collection, absent in 8.0.
            const copy = {...index}; delete copy.ns; return copy;
        }).sort((a, b) => a.name.localeCompare(b.name));
        const documents = [];
        collection.find().forEach(doc => documents.push(hash(doc)));
        documents.sort();
        result.push({database: entry.name, collection: c.name, count: documents.length,
                     documents: hash(documents), indexes: hash(indexes), options: hash(c.options)});
    }
}
print(JSON.stringify(result));
