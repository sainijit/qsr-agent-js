import React, { useState } from 'react';
import { Send, CheckCircle2, AlertCircle, ChevronDown } from 'lucide-react';
import { WhatsAppLineInfo } from '../types';

interface ReplyComposerProps {
  lines: WhatsAppLineInfo[];
  recipientPhone: string;
  onSendMessage: (payload: { recipient_phone: string; message_text: string; sender_number_id?: string }) => Promise<void>;
}

const TEMPLATES = [
  { label: 'Order confirmed', text: 'Namaste! Aapka order confirm kar liya hai. Dispatch kal subah hoga.' },
  { label: 'Payment received', text: 'Payment receive ho gaya hai. Ledger update kar diya hai. Dhanyawad!' },
  { label: 'Stock available', text: 'Current stock available hai. Quotation share kar rahe hain.' },
  { label: 'Dispatched', text: 'Vehicle dispatch ho chuka hai. Driver contact details bhej rahe hain.' },
];

export const ReplyComposer: React.FC<ReplyComposerProps> = ({ lines, recipientPhone, onSendMessage }) => {
  const [selectedNumberId, setSelectedNumberId] = useState<string>(lines[0]?.number_id || '');
  const [targetPhone, setTargetPhone] = useState<string>(recipientPhone || '919916395385');
  const [messageText, setMessageText] = useState('');
  const [isSending, setIsSending] = useState(false);
  const [feedback, setFeedback] = useState<{ type: 'success' | 'error'; message: string } | null>(null);

  React.useEffect(() => {
    if (recipientPhone) setTargetPhone(recipientPhone);
  }, [recipientPhone]);

  React.useEffect(() => {
    if (lines.length > 0 && (!selectedNumberId || !lines.some((l) => l.number_id === selectedNumberId))) {
      setSelectedNumberId(lines[0].number_id);
    }
  }, [lines, selectedNumberId]);

  const canSend = Boolean(messageText.trim() && targetPhone.trim() && !isSending);

  const handleSend = async () => {
    const phone = targetPhone.trim();
    if (!canSend) return;
    try {
      setIsSending(true);
      setFeedback(null);
      await onSendMessage({ recipient_phone: phone, message_text: messageText.trim(), sender_number_id: selectedNumberId || undefined });
      setMessageText('');
      setFeedback({ type: 'success', message: `Message queued for +${phone}` });
      setTimeout(() => setFeedback(null), 3000);
    } catch (err: any) {
      setFeedback({ type: 'error', message: err.message || 'Failed to send message.' });
    } finally {
      setIsSending(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div className="border-t border-slate-200 bg-white px-5 py-4">
      <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
        <label className="flex items-center gap-2">
          <span className="text-slate-500">To</span>
          <input
            type="text"
            value={targetPhone}
            onChange={(e) => setTargetPhone(e.target.value)}
            placeholder="919916395385"
            className="w-36 rounded-md border border-slate-200 bg-slate-50 px-2 py-1 font-mono text-sm text-slate-800 focus:border-brand-500 focus:bg-white focus:outline-none focus:ring-4 focus:ring-brand-500/10"
          />
        </label>
        <label className="flex items-center gap-2">
          <span className="text-slate-500">From</span>
          <span className="relative">
            <select
              value={selectedNumberId}
              onChange={(e) => setSelectedNumberId(e.target.value)}
              className="cursor-pointer appearance-none rounded-md border border-slate-200 bg-slate-50 py-1 pl-2 pr-8 text-sm text-slate-800 focus:border-brand-500 focus:bg-white focus:outline-none focus:ring-4 focus:ring-brand-500/10"
            >
              {lines.map((l) => (
                <option key={l.number_id} value={l.number_id}>
                  {l.assigned_worker} · {l.display_phone_number}
                </option>
              ))}
            </select>
            <ChevronDown className="pointer-events-none absolute right-2 top-1.5 h-4 w-4 text-slate-400" />
          </span>
        </label>
      </div>

      <div className="rounded-xl border border-slate-300 bg-white shadow-sm transition focus-within:border-brand-500 focus-within:ring-4 focus-within:ring-brand-500/10">
        <textarea
          rows={2}
          value={messageText}
          onChange={(e) => setMessageText(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Write a reply…"
          className="block w-full resize-none rounded-t-xl border-0 bg-transparent px-3.5 pt-3 text-sm text-slate-900 placeholder:text-slate-400 focus:outline-none"
        />
        <div className="flex items-center gap-2 px-2.5 pb-2.5 pt-1">
          <div className="flex min-w-0 flex-1 items-center gap-1.5 overflow-x-auto scrollbar-none">
            {TEMPLATES.map((t) => (
              <button
                key={t.label}
                type="button"
                onClick={() => setMessageText(t.text)}
                title={t.text}
                className="whitespace-nowrap rounded-full border border-slate-200 px-2.5 py-1 text-xs font-medium text-slate-600 transition hover:border-brand-200 hover:bg-brand-50 hover:text-brand-700"
              >
                {t.label}
              </button>
            ))}
          </div>
          <span className="hidden text-xs text-slate-400 md:inline">Ctrl + Enter</span>
          <button type="button" onClick={handleSend} disabled={!canSend} className="btn-primary px-3 py-1.5">
            <Send className="h-4 w-4" />
            {isSending ? 'Sending…' : 'Send'}
          </button>
        </div>
      </div>

      {feedback && (
        <div
          role="status"
          className={`mt-3 flex items-center gap-2 rounded-lg px-3 py-2 text-sm ${
            feedback.type === 'success' ? 'bg-emerald-50 text-emerald-800' : 'bg-rose-50 text-rose-800'
          }`}
        >
          {feedback.type === 'success' ? <CheckCircle2 className="h-4 w-4 text-emerald-600" /> : <AlertCircle className="h-4 w-4 text-rose-600" />}
          {feedback.message}
        </div>
      )}
    </div>
  );
};
