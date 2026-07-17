import type { SpecGapReport, SpecReportResponse } from '../api/types';
import { humanizeReportText } from '../components/MarkdownReport';

function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (character) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  })[character] || character);
}

function safeHttpUrl(value: string): string | null {
  try {
    const url = new URL(value);
    return url.protocol === 'https:' || url.protocol === 'http:' ? url.toString() : null;
  } catch {
    return null;
  }
}

function renderInline(value: string): string {
  const text = humanizeReportText(value);
  const pattern = /\[([^\]]+)]\((https?:\/\/[^)\s]+)\)|\*\*([^*]+)\*\*|(https?:\/\/[^\s]+)/g;
  const output: string[] = [];
  let cursor = 0;
  let match: RegExpExecArray | null;

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > cursor) output.push(escapeHtml(text.slice(cursor, match.index)));
    if (match[3]) {
      output.push(`<strong>${escapeHtml(match[3])}</strong>`);
    } else {
      const rawUrl = match[2] || match[4];
      const href = safeHttpUrl(rawUrl);
      const label = humanizeReportText(match[1] || rawUrl);
      output.push(href
        ? `<a href="${escapeHtml(href)}" target="_blank" rel="noopener noreferrer">${escapeHtml(label)}</a>`
        : escapeHtml(label));
    }
    cursor = match.index + match[0].length;
  }
  if (cursor < text.length) output.push(escapeHtml(text.slice(cursor)));
  return output.join('');
}

function markdownToSafeHtml(markdown: string): string {
  const lines = markdown.split(/\r?\n/);
  const content: string[] = [];

  for (let index = 0; index < lines.length;) {
    const raw = lines[index].trim();
    if (!raw) {
      index += 1;
      continue;
    }

    const heading = raw.match(/^(#{1,4})\s+(.+)$/);
    if (heading) {
      const level = heading[1].length;
      content.push(`<h${level}>${renderInline(heading[2])}</h${level}>`);
      index += 1;
      continue;
    }

    if (/^[-*]\s+/.test(raw)) {
      const items: string[] = [];
      while (index < lines.length) {
        const item = lines[index].trim().match(/^[-*]\s+(.+)$/);
        if (!item) break;
        items.push(`<li>${renderInline(item[1])}</li>`);
        index += 1;
      }
      content.push(`<ul>${items.join('')}</ul>`);
      continue;
    }

    if (/^\d+[.)]\s+/.test(raw)) {
      const items: string[] = [];
      while (index < lines.length) {
        const item = lines[index].trim().match(/^\d+[.)]\s+(.+)$/);
        if (!item) break;
        items.push(`<li>${renderInline(item[1])}</li>`);
        index += 1;
      }
      content.push(`<ol>${items.join('')}</ol>`);
      continue;
    }

    content.push(`<p>${renderInline(raw)}</p>`);
    index += 1;
  }

  return content.join('\n');
}

function groupLines(title: string, groups: SpecGapReport['satisfiedCertificationGroups']): string[] {
  return [
    `## ${title}`,
    ...(groups.length
      ? groups.map((group) => `- ${group.groupName}: ${group.certificationNames.join(', ')}`)
      : ['- 해당 항목이 없습니다.']),
    '',
  ];
}

function structuredFallbackMarkdown(report: SpecGapReport): string {
  const certifications = report.normalizedCertifications.map((item) => {
    const name = item.normalizedName || item.inputName;
    return item.qnetUrl ? `- [${name}](${item.qnetUrl})` : `- ${name}`;
  });
  const priorities = report.priorityActions.map(
    (item) => `${item.priority}. ${item.itemName}: ${item.reason}`,
  );

  return [
    `# ${report.targetTrade} 스펙 보완 보고서`,
    '',
    '## 분석 범위',
    report.analysisScope,
    '',
    '## 보유 자격',
    ...(certifications.length ? certifications : ['- 등록된 자격이 없습니다.']),
    '',
    ...groupLines('충족한 자격 요건', report.satisfiedCertificationGroups),
    ...groupLines('부족한 핵심 자격 요건', report.missingCoreCertificationGroups),
    ...groupLines('추천 자격', report.recommendedCertificationGroups),
    '## 능력 커버리지',
    `${report.abilityCoverage.percentage}% (${report.abilityCoverage.matched}/${report.abilityCoverage.required})`,
    '',
    '### 보유 능력',
    ...(report.matchedAbilities.length
      ? report.matchedAbilities.map((item) => `- ${item.abilityName}`)
      : ['- 확인된 능력이 없습니다.']),
    '',
    '### 보완할 능력',
    ...(report.missingAbilities.length
      ? report.missingAbilities.map((item) => `- ${item.abilityName}`)
      : ['- 추가 보완 항목이 없습니다.']),
    '',
    '## 우선 보완 순서',
    ...(priorities.length ? priorities : ['- 우선 보완 항목이 없습니다.']),
    '',
    '## 주의사항과 확인 필요 항목',
    ...[...report.limitations, ...report.humanReviewItems].map((item) => `- ${item}`),
  ].join('\n');
}

export function buildReportDownloadHtml(result: SpecReportResponse): string {
  const report = result.report;
  const markdown = result.markdown?.trim() || structuredFallbackMarkdown(report);
  const generatedAt = new Date(report.generatedAt).toLocaleString('ko-KR');
  const title = `${report.targetTrade} 스펙 보완 보고서`;

  return `<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
  <title>${escapeHtml(title)}</title>
  <style>
    @page { size: A4; margin: 18mm; }
    :root { color-scheme: light; font-family: Pretendard, "Noto Sans KR", "Apple SD Gothic Neo", Arial, sans-serif; }
    body { max-width: 760px; margin: 0 auto; padding: 36px 28px 64px; color: #1f2937; line-height: 1.75; word-break: keep-all; overflow-wrap: anywhere; }
    .meta { display: flex; justify-content: space-between; gap: 16px; padding-bottom: 18px; margin-bottom: 26px; border-bottom: 2px solid #15803d; color: #6b7280; font-size: 12px; }
    .brand { color: #15803d; font-weight: 700; }
    h1 { margin: 0 0 22px; color: #111827; font-size: 28px; line-height: 1.35; }
    h2 { margin: 34px 0 12px; padding-bottom: 7px; border-bottom: 1px solid #e5e7eb; color: #111827; font-size: 19px; }
    h3 { margin: 24px 0 8px; color: #1f2937; font-size: 16px; }
    h4 { margin: 18px 0 6px; color: #166534; font-size: 14px; }
    p { margin: 8px 0; font-size: 14px; }
    ul, ol { margin: 8px 0; padding-left: 24px; }
    li { margin: 5px 0; font-size: 14px; }
    a { color: #15803d; text-decoration: underline; text-underline-offset: 2px; }
    strong { color: #111827; }
    .footer { margin-top: 42px; padding-top: 14px; border-top: 1px solid #e5e7eb; color: #9ca3af; font-size: 11px; }
    @media print { body { max-width: none; padding: 0; } a { color: #166534; } }
  </style>
</head>
<body>
  <div class="meta"><span class="brand">CrewMate</span><span>생성일 ${escapeHtml(generatedAt)}</span></div>
  ${markdownToSafeHtml(markdown)}
  <div class="footer">이 보고서는 지원서와 확인된 근거를 바탕으로 생성된 참고 자료입니다.</div>
</body>
</html>`;
}

function reportFilename(report: SpecGapReport): string {
  const date = Number.isNaN(new Date(report.generatedAt).getTime())
    ? ''
    : new Date(report.generatedAt).toISOString().slice(0, 10).replace(/-/g, '');
  const trade = report.targetTrade.replace(/[\\/:*?"<>|]/g, '').trim() || '스펙';
  return `${trade}-스펙-보완-보고서${date ? `-${date}` : ''}.html`;
}

export function downloadSpecReport(result: SpecReportResponse): void {
  const blob = new Blob([buildReportDownloadHtml(result)], { type: 'text/html;charset=utf-8' });
  const href = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = href;
  anchor.download = reportFilename(result.report);
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(href), 0);
}
