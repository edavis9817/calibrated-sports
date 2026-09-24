// f-20 census harness ONLY (scratch clone, never committed): every SITE_DATA read goes to
// the PRODUCTION /data/ endpoint, so pages render against production-served data.
const PROD = process.env.CENSUS_PROD ?? "https://calibratedsports-web.ethanad17.workers.dev";
const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36";
const cache = new Map<string, string | null>();
export const prodBucket = {
  async get(key: string) {
    let text = cache.get(key);
    if (text === undefined) {
      const r = await fetch(`${PROD}/data/${key}`, { headers: { "user-agent": UA } });
      if (r.status === 404) text = null;
      else if (!r.ok) throw new Error(`prod ${r.status} for ${key}`);
      else text = await r.text();
      cache.set(key, text);
      try { require("fs").appendFileSync(process.env.CENSUS_LOG ?? "census-keys.log", `${r.status}\t${key}\n`); } catch {}
    }
    if (text === null) return null;
    const t = text;
    return { text: async () => t, body: t, httpEtag: undefined } as any;
  },
};
