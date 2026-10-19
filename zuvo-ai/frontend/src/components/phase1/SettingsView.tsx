import React, { useState } from 'react';
import { FileUp, CheckCircle2, AlertTriangle, Upload, RotateCcw } from 'lucide-react';
import { PricingUploadResult, SettingsConfig } from '../../types';
import { fetchSettingsConfig, uploadPricing } from '../../services/api';
import { ErrorBox, Loading, Pill, SectionCard, fmtDateTime, fmtInr, pretty, useAsync } from './ui';

type Pane = 'config' | 'skill' | 'json';

export const SettingsView: React.FC = () => {
  const cfg = useAsync(fetchSettingsConfig, []);
  const [pane, setPane] = useState<Pane>('config');

  if (cfg.loading && !cfg.data) return <Loading />;
  if (cfg.error && !cfg.data) return <ErrorBox message={cfg.error} onRetry={() => cfg.reload()} />;
  const d = cfg.data!;

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <SectionCard
          className="lg:col-span-2"
          title={`Tenant bundle · ${d.slug}`}
          actions={
            <div className="inline-flex rounded-lg border border-slate-200 bg-slate-50 p-0.5">
              {(['config', 'skill', 'json'] as Pane[]).map((p) => (
                <button
                  key={p}
                  onClick={() => setPane(p)}
                  className={`rounded-md px-2.5 py-1 text-xs font-medium ${pane === p ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-800'}`}
                >
                  {p === 'config' ? 'config.yaml' : p === 'skill' ? 'skill.md' : 'Parsed JSON'}
                </button>
              ))}
            </div>
          }
        >
          <pre className="max-h-[520px] overflow-auto whitespace-pre-wrap bg-slate-50/60 p-5 font-mono text-xs leading-relaxed text-slate-800">
            {pane === 'config' ? d.config_yaml : pane === 'skill' ? d.skill_md || '(no skill file)' : JSON.stringify(d.config, null, 2)}
          </pre>
        </SectionCard>

        <div className="space-y-4">
          <SectionCard title="Team">
            <ul className="divide-y divide-slate-100">
              {d.workers.map((w) => (
                <li key={w.id} className="flex items-center justify-between px-5 py-2.5 text-sm">
                  <span className="font-medium text-slate-800">{w.name}</span>
                  <Pill>{pretty(w.role)}</Pill>
                </li>
              ))}
            </ul>
          </SectionCard>
          <SectionCard title="Knowledge">
            {d.knowledge.length === 0 ? (
              <p className="px-5 py-4 text-sm text-slate-400">No knowledge documents</p>
            ) : (
              <ul className="divide-y divide-slate-100">
                {d.knowledge.map((k) => (
                  <li key={k.path} className="px-5 py-2.5 text-sm">
                    <p className="font-medium text-slate-800">{k.title}</p>
                    <p className="text-xs text-slate-500">{k.path} · {k.chars} chars</p>
                  </li>
                ))}
              </ul>
            )}
          </SectionCard>
          <SectionCard title="Checksums">
            <dl className="space-y-1 px-5 py-3 text-xs">
              {Object.entries(d.checksums).map(([k, v]) => (
                <div key={k} className="flex gap-2">
                  <dt className="w-20 shrink-0 text-slate-500">{k}</dt>
                  <dd className="truncate font-mono text-slate-700" title={v}>{v}</dd>
                </div>
              ))}
            </dl>
          </SectionCard>
        </div>
      </div>

      <PricingCard settings={d} onImported={() => cfg.reload(true)} />
    </div>
  );
};

const priceText = (p: { price: number | null; price_min: number | null; price_max: number | null }) =>
  p.price !== null && p.price !== undefined ? fmtInr(p.price) : p.price_min !== null || p.price_max !== null ? `${fmtInr(p.price_min)} – ${fmtInr(p.price_max)}` : '—';

const PricingCard: React.FC<{ settings: SettingsConfig; onImported: () => void }> = ({ settings, onImported }) => {
  const [csv, setCsv] = useState('');
  const [fileName, setFileName] = useState('dashboard-upload.csv');
  const [preview, setPreview] = useState<PricingUploadResult | null>(null);
  const [imported, setImported] = useState<PricingUploadResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const pl = settings.price_list;

  const onFile = async (f: File | undefined) => {
    if (!f) return;
    setFileName(f.name);
    setCsv(await f.text());
    setPreview(null);
    setImported(null);
  };

  const run = async (dryRun: boolean) => {
    setBusy(true);
    setErr(null);
    try {
      const r = await uploadPricing(csv, fileName, dryRun);
      if (dryRun) {
        setPreview(r);
        setImported(null);
      } else {
        setImported(r);
        setPreview(null);
        if (r.import) onImported();
      }
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };

  const reset = () => {
    setCsv('');
    setPreview(null);
    setImported(null);
    setErr(null);
  };

  const diff = preview?.diff;
  const noChanges = diff && diff.added.length === 0 && diff.removed.length === 0 && diff.changed.length === 0;

  return (
    <SectionCard
      title="Pricing"
      actions={
        pl && (
          <span className="text-xs text-slate-500">
            v{pl.version} · {pl.rows} rows · {pl.source_file || '—'} · {pl.imported_by || '—'} · {fmtDateTime(pl.imported_at)}
          </span>
        )
      }
    >
      <div className="grid grid-cols-1 gap-6 p-5 xl:grid-cols-2">
        <div className="space-y-3">
          <h3 className="eyebrow">Upload a new price list</h3>
          <p className="text-xs text-slate-500">
            CSV with columns <code className="font-mono">sku,name</code> (required) plus optional price, price_min, price_max, category, unit, valid_until, aliases, attrs.
            A dry-run shows exactly what will change before anything is imported.
          </p>
          <label className="flex cursor-pointer items-center justify-center gap-2 rounded-lg border-2 border-dashed border-slate-300 bg-slate-50 px-4 py-5 text-sm text-slate-600 hover:border-brand-400 hover:bg-brand-50/40">
            <FileUp className="h-5 w-5 text-brand-600" />
            <span>{csv ? fileName : 'Choose a CSV file'}</span>
            <input type="file" accept=".csv,text/csv" className="hidden" onChange={(e) => onFile(e.target.files?.[0])} />
          </label>
          <textarea
            className="field min-h-[120px] font-mono text-xs"
            placeholder="…or paste CSV here"
            value={csv}
            onChange={(e) => {
              setCsv(e.target.value);
              setPreview(null);
              setImported(null);
            }}
          />
          <div className="flex flex-wrap gap-2">
            <button className="btn-secondary" disabled={busy || !csv.trim()} onClick={() => run(true)}>
              <Upload className="h-4 w-4" /> Dry run
            </button>
            {preview && preview.errors.length === 0 && (
              <button className="btn-primary" disabled={busy || !!noChanges} onClick={() => run(false)}>
                <CheckCircle2 className="h-4 w-4" /> Confirm import
              </button>
            )}
            {(csv || preview || imported) && (
              <button className="btn-ghost" disabled={busy} onClick={reset}>
                <RotateCcw className="h-4 w-4" /> Reset
              </button>
            )}
          </div>
          {err && <ErrorBox message={err} />}
          {imported && (
            imported.import ? (
              <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
                {imported.import.changed === false
                  ? `No changes — price list v${String(imported.import.version)} is already active.`
                  : `Imported ${String(imported.import.rows ?? imported.rows)} rows as price list v${String(imported.import.version)}.`}
              </div>
            ) : (
              <ErrorBox message={`Import blocked: ${imported.errors.join('; ') || 'unknown error'}`} />
            )
          )}
          {preview && (
            <div className="space-y-3">
              <div className="flex flex-wrap gap-2 text-xs">
                <Pill>{preview.rows} rows parsed</Pill>
                <Pill tone="bg-emerald-50 text-emerald-700">+{preview.diff.added.length} added</Pill>
                <Pill tone="bg-amber-50 text-amber-800">~{preview.diff.changed.length} changed</Pill>
                <Pill tone="bg-rose-50 text-rose-700">−{preview.diff.removed.length} removed</Pill>
              </div>
              {preview.errors.length > 0 && (
                <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">
                  <p className="mb-1 font-medium">Fix these errors before importing:</p>
                  <ul className="list-disc pl-5 text-xs">{preview.errors.map((e) => <li key={e}>{e}</li>)}</ul>
                </div>
              )}
              {preview.warnings.length > 0 && (
                <div className="flex gap-2 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-800">
                  <AlertTriangle className="h-4 w-4 shrink-0" />
                  <ul>{preview.warnings.map((w) => <li key={w}>{w}</li>)}</ul>
                </div>
              )}
              {noChanges && preview.errors.length === 0 && <p className="text-sm text-slate-500">No differences from the active price list.</p>}
              {diff && !noChanges && (
                <div className="overflow-x-auto rounded-lg border border-slate-200">
                  <table className="w-full text-xs">
                    <thead className="bg-slate-50 text-left uppercase tracking-wide text-slate-500">
                      <tr>
                        <th className="px-3 py-2">Change</th>
                        <th className="px-3 py-2">SKU</th>
                        <th className="px-3 py-2">Before</th>
                        <th className="px-3 py-2">After</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      {diff.added.map((s) => (
                        <tr key={`a-${s}`} className="bg-emerald-50/40">
                          <td className="px-3 py-2"><Pill tone="bg-emerald-100 text-emerald-800">Added</Pill></td>
                          <td className="px-3 py-2 font-mono">{s}</td>
                          <td className="px-3 py-2 text-slate-400">—</td>
                          <td className="px-3 py-2">new item</td>
                        </tr>
                      ))}
                      {diff.changed.map((c) => (
                        <tr key={`c-${c.sku}`} className="bg-amber-50/40">
                          <td className="px-3 py-2"><Pill tone="bg-amber-100 text-amber-800">Changed</Pill></td>
                          <td className="px-3 py-2 font-mono">{c.sku}</td>
                          <td className="px-3 py-2">{c.old.name !== c.new.name && <div className="text-slate-500">{c.old.name}</div>}{priceText(c.old)}</td>
                          <td className="px-3 py-2 font-medium">{c.old.name !== c.new.name && <div>{c.new.name}</div>}{priceText(c.new)}</td>
                        </tr>
                      ))}
                      {diff.removed.map((s) => (
                        <tr key={`r-${s}`} className="bg-rose-50/40">
                          <td className="px-3 py-2"><Pill tone="bg-rose-100 text-rose-800">Removed</Pill></td>
                          <td className="px-3 py-2 font-mono">{s}</td>
                          <td className="px-3 py-2">existing item</td>
                          <td className="px-3 py-2 text-slate-400">—</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
        </div>

        <div>
          <h3 className="eyebrow mb-3">Active price list</h3>
          {settings.price_items.length === 0 ? (
            <p className="text-sm text-slate-400">No active price list</p>
          ) : (
            <div className="max-h-[480px] overflow-auto rounded-lg border border-slate-200">
              <table className="w-full text-xs">
                <thead className="sticky top-0 bg-slate-50 text-left uppercase tracking-wide text-slate-500">
                  <tr>
                    <th className="px-3 py-2">SKU</th>
                    <th className="px-3 py-2">Name</th>
                    <th className="px-3 py-2">Category</th>
                    <th className="px-3 py-2 text-right">Price</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {settings.price_items.map((i) => (
                    <tr key={i.sku}>
                      <td className="px-3 py-2 font-mono text-slate-600">{i.sku}</td>
                      <td className="px-3 py-2 text-slate-800">
                        {i.name}
                        {i.aliases && i.aliases.length > 0 && <div className="text-[10px] text-slate-400">{i.aliases.join(', ')}</div>}
                      </td>
                      <td className="px-3 py-2 text-slate-600">{i.category || '—'}</td>
                      <td className="whitespace-nowrap px-3 py-2 text-right tabular-nums">
                        {priceText(i)}
                        {i.unit && i.unit !== 'unit' ? ` / ${i.unit}` : ''}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </SectionCard>
  );
};
