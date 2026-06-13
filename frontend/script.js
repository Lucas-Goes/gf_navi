const canvas = document.getElementById('fairy-canvas');
const ctx = canvas ? canvas.getContext('2d') : null;
let particles = [];

function resize() {
if (!canvas) return;
canvas.width = window.innerWidth;
canvas.height = window.innerHeight;
}

window.addEventListener('resize', resize);
resize();

class Particle {
constructor() {
this.x = Math.random() * canvas.width;
this.y = Math.random() * canvas.height;
this.size = Math.random() * 3 + 1;
this.speedX = (Math.random() - 0.5) * 0.3;
this.speedY = (Math.random() - 0.5) * 0.3 - 0.1;
this.opacity = Math.random() * 0.4 + 0.1;
this.hue = 185 + Math.random() * 15;
this.saturation = 70 + Math.random() * 20;
}

update() {
this.x += this.speedX;
this.y += this.speedY;
this.opacity += (Math.random() - 0.5) * 0.01;
this.opacity = Math.max(0.05, Math.min(0.5, this.opacity));

if (this.x > canvas.width) this.x = 0;
if (this.x < 0) this.x = canvas.width;
if (this.y > canvas.height) this.y = 0;
if (this.y < 0) this.y = canvas.height;
}

draw() {
ctx.beginPath();
ctx.arc(this.x, this.y, this.size, 0, Math.PI * 2);
ctx.fillStyle = `hsla(${this.hue}, ${this.saturation}%, 70%, ${this.opacity})`;
ctx.fill();

if (this.size > 1.5) {
ctx.beginPath();
ctx.arc(this.x, this.y, this.size * 2.5, 0, Math.PI * 2);
ctx.fillStyle = `hsla(${this.hue}, ${this.saturation}%, 80%, ${this.opacity * 0.15})`;
ctx.fill();
}
}
}

function init() {
for (let i = 0; i < 60; i++) {
particles.push(new Particle());
}
}

function animate() {
if (!ctx || !canvas) return;
ctx.clearRect(0, 0, canvas.width, canvas.height);
particles.forEach(p => {
p.update();
p.draw();
});

for (let a = 0; a < particles.length; a++) {
for (let b = a; b < particles.length; b++) {
let distance = ((particles[a].x - particles[b].x) ** 2 + (particles[a].y - particles[b].y) ** 2) ** 0.5;
if (distance < 120) {
let hue = (particles[a].hue + particles[b].hue) / 2;
ctx.strokeStyle = `hsla(${hue}, 70%, 75%, ${0.06 * (1 - distance / 120)})`;
ctx.lineWidth = 0.5;
ctx.beginPath();
ctx.moveTo(particles[a].x, particles[a].y);
ctx.lineTo(particles[b].x, particles[b].y);
ctx.stroke();
}
}
}

requestAnimationFrame(animate);
}

try { init(); animate(); } catch (e) { console.error('BG particles init failed:', e); }

// Logo Energy Particles
const energyCanvas = document.getElementById('logo-energy');
const ectx = energyCanvas ? energyCanvas.getContext('2d') : null;
let energyParticles = [];
let energyRunning = true;

function resizeEnergy() {
if (!energyCanvas || !ectx) return;
const parent = energyCanvas.parentElement;
if (!parent) return;
const rect = parent.getBoundingClientRect();
if (rect.width > 0 && rect.height > 0) {
energyCanvas.width = rect.width;
energyCanvas.height = rect.height;
}
}

window.addEventListener('resize', resizeEnergy);
resizeEnergy();

const ENERGY_COLORS = [
'hsla(185, 90%, 85%,',
'hsla(195, 95%, 90%,',
'hsla(180, 85%, 80%,',
'hsla(190, 100%, 95%,',
'hsla(175, 80%, 88%,',
];

class EnergyParticle {
constructor() {
this.reset();
}

reset() {
this.cx = energyCanvas.width / 2;
this.cy = energyCanvas.height / 2;
this.angle = Math.random() * Math.PI * 2;
this.speed = 0.4 + Math.random() * 1.5;
this.maxDist = 50 + Math.random() * 160;
this.dist = 0;
this.size = 2 + Math.random() * 4;
this.opacity = 0.5 + Math.random() * 0.5;
this.color = ENERGY_COLORS[Math.floor(Math.random() * ENERGY_COLORS.length)];
this.delay = Math.random() * 40;
this.frame = 0;
}

update() {
this.frame++;
if (this.frame < this.delay) return;
this.dist += this.speed;
const progress = this.dist / this.maxDist;
this.x = this.cx + Math.cos(this.angle) * this.dist;
this.y = this.cy + Math.sin(this.angle) * this.dist;
this.currentOpacity = this.opacity * (1 - progress) * (1 - progress);
this.currentSize = this.size * (1 - progress * 0.5);
if (this.dist >= this.maxDist) this.reset();
}

draw() {
if (this.frame < this.delay) return;
ectx.beginPath();
ectx.arc(this.x, this.y, this.currentSize, 0, Math.PI * 2);
ectx.fillStyle = `${this.color} ${this.currentOpacity})`;
ectx.fill();

// outer glow
ectx.beginPath();
ectx.arc(this.x, this.y, this.currentSize * 4, 0, Math.PI * 2);
ectx.fillStyle = `${this.color} ${this.currentOpacity * 0.2})`;
ectx.fill();

// extra soft glow
ectx.beginPath();
ectx.arc(this.x, this.y, this.currentSize * 8, 0, Math.PI * 2);
ectx.fillStyle = `${this.color} ${this.currentOpacity * 0.06})`;
ectx.fill();
}
}

function initEnergy() {
for (let i = 0; i < 50; i++) {
energyParticles.push(new EnergyParticle());
}
}

function animateEnergy() {
if (!energyRunning || !ectx || !energyCanvas) return;
ectx.clearRect(0, 0, energyCanvas.width, energyCanvas.height);
energyParticles.forEach(p => {
p.update();
p.draw();
});
requestAnimationFrame(animateEnergy);
}

try { initEnergy(); animateEnergy(); } catch (e) { console.error('Energy particles init failed:', e); }

const setupView = document.getElementById('setup-view');
const messagesContainer = document.getElementById('messages-container');
const chatInput = document.getElementById('chat-input');
const sendBtn = document.getElementById('send-button');
const chatInputArea = document.getElementById('chat-input-area');
const novoChatBtn = document.getElementById('novo-chat-btn');

const welcomeHTML = messagesContainer.innerHTML;

function showChat() {
  setupView.classList.add('animate-fade-out');
  setTimeout(() => {
    setupView.style.display = 'none';
    setupView.classList.remove('animate-fade-out');
    messagesContainer.style.display = 'block';
    chatInputArea.style.display = 'block';
    messagesContainer.offsetHeight;
    chatInputArea.offsetHeight;
    messagesContainer.classList.add('animate-fade-in');
    chatInputArea.classList.add('animate-fade-in');
    messagesContainer.scrollTop = messagesContainer.scrollHeight;
  }, 250);
}

function hideChat() {
  messagesContainer.classList.remove('animate-fade-in');
  chatInputArea.classList.remove('animate-fade-in');
  messagesContainer.innerHTML = welcomeHTML;
  chatInput.value = '';
  chatInput.style.height = 'auto';
  setupView.style.display = 'flex';
  messagesContainer.style.display = 'none';
  chatInputArea.style.display = 'none';
  resizeEnergy();
  energyParticles = [];
  initEnergy();
}

document.querySelectorAll('#setup-view .group').forEach(card => {
card.addEventListener('click', showChat);
});

novoChatBtn.addEventListener('click', hideChat);

// Theme Toggle
const themeToggle = document.getElementById('theme-toggle');
const themeIcon = document.getElementById('theme-icon');

function getTheme() {
return localStorage.getItem('navi-theme') || 'light';
}

function setTheme(theme) {
document.documentElement.setAttribute('data-theme', theme);
if (themeIcon) themeIcon.textContent = theme === 'dark' ? 'light_mode' : 'dark_mode';
localStorage.setItem('navi-theme', theme);
}

try { setTheme(getTheme()); } catch (e) { console.error('Theme init failed:', e); }

if (themeToggle) {
themeToggle.addEventListener('click', () => {
const current = document.documentElement.getAttribute('data-theme');
setTheme(current === 'dark' ? 'light' : 'dark');
});
}

if (!chatInput || !sendBtn || !messagesContainer || !chatInputArea || !setupView || !novoChatBtn) {
console.error('Chat UI elements missing — check HTML IDs');
}

chatInput.addEventListener('input', function() {
this.style.height = 'auto';
this.style.height = (this.scrollHeight) + 'px';
});

chatInput.addEventListener('keydown', (e) => {
if (e.key === 'Enter' && !e.shiftKey) {
e.preventDefault();
sendBtn.click();
}
});

sendBtn.addEventListener('click', () => {
if (chatInput.value.trim() === '') return;

const msg = chatInput.value;
const msgHtml = `
<div class="flex flex-col gap-2 max-w-[80%] mb-8 bubble-enter" style="margin-left: auto;">
  <div class="flex items-center gap-3 justify-end mr-1">
    <span class="text-[11px] tracking-widest text-warm-muted/60 uppercase" style="font-family: 'Space Grotesk', sans-serif;">Você <span class="opacity-40 ml-2 font-body italic lowercase">${new Date().getHours()}:${String(new Date().getMinutes()).padStart(2, '0')}</span></span>
    <div class="w-8 h-8 rounded-full glass-container flex items-center justify-center">
      <span class="material-symbols-outlined text-xs text-warm-text/60">person</span>
    </div>
  </div>
  <div class="glass-bubble-user p-6 rounded-3xl rounded-tr-none relative text-right" style="overflow-wrap: break-word; word-break: break-word;">
    <p class="text-warm-text/90 leading-relaxed text-lg">${msg}</p>
  </div>
</div>
`;

messagesContainer.insertAdjacentHTML('beforeend', msgHtml);
chatInput.value = '';
chatInput.style.height = 'auto';
messagesContainer.scrollTop = messagesContainer.scrollHeight;

setTimeout(() => {
const naviResponses = [
  `Ótimo! **Entendi sua mensagem** e já estou analisando. ✨

Aqui estão alguns pontos que posso te ajudar:

- **Análise** do que você precisa
- **Sugestões** personalizadas
- **Exemplos** práticos

\`\`\`python
# Exemplo de análise
def analisar(mensagem):
    return f"Processei: {mensagem}"
\`\`\`

> Fique à vontade para perguntar mais! 🌟`,
  `Recebi sua mensagem! Deixa eu ver aqui... 🌟

Processando com **carinho**! Aqui vai uma prévia:

\`\`\`javascript
// Resposta inteligente
const navi = {
  status: "pronta",
  missao: "ajudar você"
};
console.log(\`Navi \${navi.status}\`);
\`\`\`

📌 **Dica**: Quanto mais detalhes você der, melhor posso ajudar!`,
  `Maravilha! **Já sei o que fazer**! 🚀

Aqui está um resumo do que pensei:

1. **Analisar** seu contexto
2. **Buscar** as melhores soluções  
3. **Apresentar** de forma clara

\`\`\`typescript
interface Resposta {
  mensagem: string;
  confianca: number;
}

const resposta: Resposta = {
  mensagem: "Pronta para ajudar!",
  confianca: 0.99
};
\`\`\`

✨ Sempre aqui para o que precisar!`
];

// Simple markdown parser
function parseMarkdown(md) {
  let html = md;

  // Code blocks (must process first)
  html = html.replace(/```(\w+)?\n([\s\S]*?)```/g, (match, lang, code) => {
    const language = lang || 'plaintext';
    const escaped = code
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
    return `<pre><code class="language-${language}">${escaped}</code></pre>`;
  });

  // Bold
  html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');

  // Italic (single * or _)
  html = html.replace(/\*([^*]+)\*/g, '<em>$1</em>');

  // Inline code
  html = html.replace(/`([^`]+)`/g, '<code>$1</code>');

  // Blockquotes
  html = html.replace(/^> (.+)$/gm, '<blockquote>$1</blockquote>');

  // Lists
  html = html.replace(/^\d+\. (.+)$/gm, '<li>$1</li>');
  html = html.replace(/^[-*] (.+)$/gm, '<li>$1</li>');
  // Wrap consecutive <li> in <ul> or <ol>
  html = html.replace(/(<li>.*<\/li>\n?)+/g, (match) => {
    if (match.includes('<li>1.') || match.includes('<li>2.')) {
      return '<ol>' + match + '</ol>';
    }
    return '<ul>' + match + '</ul>';
  });

  // Paragraphs (wrap text that's not already wrapped)
  const lines = html.split('\n');
  const wrapped = [];
  let inBlock = false;
  for (let line of lines) {
    if (!line.trim()) {
      wrapped.push('');
      continue;
    }
    const isBlock = /^<(pre|ul|ol|blockquote|h[1-6])/.test(line.trim());
    if (isBlock) {
      if (!inBlock) wrapped.push('');
      wrapped.push(line);
      inBlock = true;
    } else {
      if (inBlock) wrapped.push('');
      wrapped.push(`<p>${line}</p>`);
      inBlock = false;
    }
  }
  html = wrapped.join('\n').replace(/\n+/g, '\n').trim();

  return html;
}

// Simple syntax highlighter (minimal)
function highlightCode() {
  document.querySelectorAll('pre code').forEach(block => {
    const text = block.textContent;
    const lang = block.className.replace('language-', '');
    
    let highlighted = text
      // Comments
      .replace(/(\/\/.*|\/\*[\s\S]*?\*\/)/g, '<span class="hljs-comment">$1</span>')
      // Strings
      .replace(/(".*?"|'.*?'|`.*?`)/g, '<span class="hljs-string">$1</span>')
      // Keywords
      .replace(/\b(const|let|var|function|return|if|else|for|while|class|interface|type|import|export|from|async|await|console\.log)\b/g, '<span class="hljs-keyword">$1</span>')
      // Types
      .replace(/\b(string|number|boolean|void|any|interface|type)\b/g, '<span class="hljs-type">$1</span>')
      // Numbers
      .replace(/\b(\d+\.?\d*)\b/g, '<span class="hljs-number">$1</span>');

    block.innerHTML = highlighted;
  });
}

const randomIndex = Math.floor(Math.random() * naviResponses.length);
const markdown = naviResponses[randomIndex];
let rendered = parseMarkdown(markdown);

const responseHtml = `
<div class="flex flex-col gap-2 max-w-[80%] mb-8 bubble-enter" style="margin-right: auto;">
  <div class="flex items-center gap-3 ml-1">
    <div class="w-8 h-8 rounded-full bg-fairy/20 flex items-center justify-center ring-1 ring-fairy/30 overflow-hidden">
      <img src="logo.png" alt="Navi" class="w-7 h-7 object-contain opacity-60" style="margin-top: 4px;" />
    </div>
    <span class="text-[11px] tracking-widest text-fairy/80 uppercase" style="font-family: 'Space Grotesk', sans-serif;">Navi <span class="opacity-40 ml-2 font-body italic lowercase">${new Date().getHours()}:${String(new Date().getMinutes()).padStart(2, '0')}</span></span>
  </div>
  <div class="glass-bubble-ai p-6 rounded-3xl rounded-tl-none relative" style="overflow-wrap: break-word; word-break: break-word;">
    <div class="prose prose-sm max-w-none text-warm-text/90 leading-relaxed text-lg [&_code]:bg-warm-text/10 [&_code]:px-2 [&_code]:py-0.5 [&_code]:rounded-lg [&_code]:text-sm [&_pre]:bg-warm-text/5 [&_pre]:p-4 [&_pre]:rounded-2xl [&_pre]:overflow-x-auto [&_pre_code]:bg-transparent [&_pre_code]:p-0 [&_blockquote]:border-l-2 [&_blockquote]:border-fairy/30 [&_blockquote]:pl-4 [&_blockquote]:text-warm-muted [&_blockquote]:italic [&_ul]:list-disc [&_ul]:pl-5 [&_ol]:list-decimal [&_ol]:pl-5 [&_h1]:text-xl [&_h1]:font-bold [&_h2]:text-lg [&_h2]:font-semibold [&_h3]:text-base [&_h3]:font-medium [&_strong]:text-warm-text [&_a]:text-fairy [&_a]:underline [&_a:hover]:opacity-80>
      ${rendered}
    </div>
  </div>
</div>
`;
messagesContainer.insertAdjacentHTML('beforeend', responseHtml);
messagesContainer.scrollTop = messagesContainer.scrollHeight;

highlightCode();
}, 800);
});
