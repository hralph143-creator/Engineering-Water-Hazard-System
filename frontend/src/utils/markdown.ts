const escapeHtml=(value:string)=>value.replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]||char))
const inline=(value:string)=>escapeHtml(value)
 .replace(/`([^`]+)`/g,'<code>$1</code>')
 .replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>')

/** 仅支持受控标题、列表、粗体和行内代码；先转义全部 HTML，避免脚本注入。 */
export function safeMarkdown(value:string){
 const blocks:string[]=[];let list:'ul'|'ol'|null=null
 const close=()=>{if(list){blocks.push(`</${list}>`);list=null}}
 for(const raw of value.split(/\r?\n/)){
  const line=raw.trimEnd()
  if(!line.trim()){close();continue}
  const heading=line.match(/^#+\s*(.+)$/)
  if(heading){close();blocks.push(`<h4>${inline(heading[1])}</h4>`);continue}
  const chineseHeading=line.match(/^[一二三四五六七八九十]+、\s*(.+)$/)
  if(chineseHeading){close();blocks.push(`<h4>${inline(line)}</h4>`);continue}
  const unordered=line.match(/^[-*]\s+(.+)$/),ordered=line.match(/^\d+[.)]\s+(.+)$/)
  if(unordered||ordered){const kind=unordered?'ul':'ol';if(list!==kind){close();list=kind;blocks.push(`<${kind}>`)}blocks.push(`<li>${inline((unordered||ordered)![1])}</li>`);continue}
  close();blocks.push(`<p>${inline(line)}</p>`)
 }
 close();return blocks.join('')
}
