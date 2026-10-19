import React, { useEffect, useState } from 'react';
import { X, Send, CheckCircle2, AlertCircle } from 'lucide-react';
import { WhatsAppLineInfo } from '../types';

interface SimulationModalProps {
  isOpen: boolean;
  onClose: () => void;
  lines: WhatsAppLineInfo[];
  onSimulate: (payload: { phone_number_id: string; sender_phone: string; sender_name: string; message_text: string }) => Promise<void>;
}

const SCENARIOS = [
  { label: 'Pipe order', customer: 'Sharma Traders (Jaipur)', phone: '+919414099999', text: '20mm CPVC pipe ke 50 box kal subah bhej do urgent.' },
  { label: 'Payment update', customer: 'Khandelwal Sanitary Hub', phone: '+919414088888', text: 'Purane bill ka payment RTGS kar diya hai ₹1.1 Lakh.' },
  { label: 'Price inquiry', customer: 'Sharma Traders (Jaipur)', phone: '+919414099999', text: 'Suresh bhai, sample wale Jaguar divertor pasand aa gaye hain, rate bhejo.' },
  { label: 'Dispatch query', customer: 'Rajputana Electricals & Pipes', phone: '+919414077777', text: 'Mukesh ji, vehicle RJ-14 kab tak dispatch hoga?' },
];

export const SimulationModal: React.FC<SimulationModalProps> = ({ isOpen, onClose, lines, onSimulate }) => {
  const [selectedPnId, setSelectedPnId] = useState<string>(lines[0]?.meta_phone_number_id || '');
  const [senderPhone, setSenderPhone] = useState(SCENARIOS[0].phone);
  const [senderName, setSenderName] = useState(SCENARIOS[0].customer);
  const [messageText, setMessageText] = useState(SCENARIOS[0].text);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [result, setResult] = useState<{ success: boolean; message: string } | null>(null);

  useEffect(() => {
    if (lines.length > 0 && !lines.some((l) => l.meta_phone_number_id === selectedPnId)) {
      setSelectedPnId(lines[0].meta_phone_number_id);
    }
  }, [lines, selectedPnId]);

  useEffect(() => {
    if (!isOpen) return;
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!messageText.trim() || !senderPhone.trim() || isSubmitting) return;
    try {
      setIsSubmitting(true);
      setResult(null);
      await onSimulate({
        phone_number_id: selectedPnId,
        sender_phone: senderPhone.trim(),
        sender_name: senderName.trim(),
        message_text: messageText.trim(),
      });
      setResult({ success: true, message: 'Message received. AI extraction is running.' });
      setTimeout(() => {
        onClose();
        setResult(null);
      }, 1200);
    } catch (err: any) {
      setResult({ success: false, message: err.message || 'Simulation failed.' });
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-slate-900/40 backdrop-blur-[2px]" onClick={onClose} />
      <div role="dialog" aria-modal="true" aria-labelledby="sim-title" className="relative w-full max-w-lg overflow-hidden rounded-2xl bg-white shadow-pop ring-1 ring-slate-200">
        <div className="flex items-start justify-between border-b border-slate-200 px-6 py-4">
          <div>
            <h3 id="sim-title" className="text-base font-semibold text-slate-900">Simulate inbound message</h3>
            <p className="mt-0.5 text-sm text-slate-500">Send a test WhatsApp message through the full pipeline.</p>
          </div>
          <button onClick={onClose} className="btn-icon -mr-2" aria-label="Close">
            <X className="h-5 w-5" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-5 px-6 pt-5">
          <div>
            <span className="label">Scenario</span>
            <div className="flex flex-wrap gap-2">
              {SCENARIOS.map((sc) => {
                const active = sc.text === messageText && sc.phone === senderPhone;
                return (
                  <button
                    key={sc.label}
                    type="button"
                    onClick={() => {
                      setSenderName(sc.customer);
                      setSenderPhone(sc.phone);
                      setMessageText(sc.text);
                    }}
                    className={`rounded-full border px-3 py-1.5 text-sm font-medium transition ${
                      active ? 'border-brand-300 bg-brand-50 text-brand-700' : 'border-slate-200 text-slate-600 hover:bg-slate-50'
                    }`}
                  >
                    {sc.label}
                  </button>
                );
              })}
            </div>
          </div>

          <div>
            <label className="label" htmlFor="sim-line">Receiving staff line</label>
            <select id="sim-line" value={selectedPnId} onChange={(e) => setSelectedPnId(e.target.value)} className="field cursor-pointer">
              {lines.map((l) => (
                <option key={l.meta_phone_number_id} value={l.meta_phone_number_id}>
                  {l.assigned_worker} · {l.display_phone_number}
                </option>
              ))}
            </select>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="label" htmlFor="sim-name">Customer</label>
              <input id="sim-name" type="text" value={senderName} onChange={(e) => setSenderName(e.target.value)} className="field" />
            </div>
            <div>
              <label className="label" htmlFor="sim-phone">Phone</label>
              <input id="sim-phone" type="text" value={senderPhone} onChange={(e) => setSenderPhone(e.target.value)} className="field font-mono" />
            </div>
          </div>

          <div>
            <label className="label" htmlFor="sim-text">Message</label>
            <textarea
              id="sim-text"
              rows={3}
              value={messageText}
              onChange={(e) => setMessageText(e.target.value)}
              placeholder="Hinglish, Hindi or English…"
              className="field resize-none"
            />
          </div>

          {result && (
            <div className={`flex items-center gap-2 rounded-lg px-3 py-2 text-sm ${result.success ? 'bg-emerald-50 text-emerald-800' : 'bg-rose-50 text-rose-800'}`}>
              {result.success ? <CheckCircle2 className="h-4 w-4 text-emerald-600" /> : <AlertCircle className="h-4 w-4 text-rose-600" />}
              {result.message}
            </div>
          )}

          <div className="-mx-6 flex justify-end gap-2 border-t border-slate-200 bg-slate-50 px-6 py-4">
            <button type="button" onClick={onClose} className="btn-secondary">Cancel</button>
            <button type="submit" disabled={!messageText.trim() || isSubmitting} className="btn-primary">
              <Send className="h-4 w-4" />
              {isSubmitting ? 'Sending…' : 'Send test message'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
