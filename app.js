const library = [
  {
    title: "OpenAI представила новое поколение агентных инструментов",
    date: "сегодня, 11:42",
    excerpt: "Новые возможности для выполнения многошаговых задач и работы с внешними сервисами.",
    tags: ["OpenAI", "agents"],
    href: "https://t.me/machineintheshell"
  },
  {
    title: "Как меняется рынок AI-кодинга",
    date: "вчера, 18:20",
    excerpt: "Разработчики переходят от автодополнения к агентам, которые берут задачу целиком.",
    tags: ["devtools", "research"],
    href: "https://t.me/machineintheshell"
  },
  {
    title: "Небольшие модели стали сильнее в реальных задачах",
    date: "3 окт.",
    excerpt: "Почему локальные и специализированные модели становятся практичным выбором.",
    tags: ["models", "local AI"],
    href: "https://t.me/machineintheshell"
  }
];

const responses = {
  default: {
    intro: "Вот что удалось найти в архиве по этому вопросу.",
    body: "В последние публикации особенно часто попадали <strong>AI-агенты, развитие моделей и инструменты для разработчиков</strong>. Главный сдвиг — от отдельных функций к системам, которые могут самостоятельно планировать несколько шагов и использовать инструменты.<sup>[1]</sup>",
    ending: "Одновременно заметен практический интерес к компактным моделям: они дешевле в запуске, проще разворачиваются локально и всё лучше справляются с узкими прикладными сценариями.<sup>[3]</sup>"
  },
  agents: {
    intro: "В архиве агенты — одна из самых обсуждаемых тем.",
    body: "Сейчас фокус смещается с простого автодополнения к <strong>агентам, которые получают задачу целиком</strong>: изучают кодовую базу, составляют план, вызывают нужные инструменты и проверяют результат.<sup>[2]</sup>",
    ending: "Самые полезные сценарии: разбор незнакомого репозитория, написание тестов, подготовка PR и работа с документацией. Важное ограничение — агенту нужен контролируемый доступ к инструментам и понятные критерии проверки результата.<sup>[1]</sup>"
  },
  models: {
    intro: "Вот краткая выжимка о моделях из материалов канала.",
    body: "Главный тренд — модели становятся не только мощнее, но и доступнее для прикладного использования. Наряду с флагманами растёт ценность <strong>небольших и специализированных моделей</strong>, особенно когда важны стоимость, приватность и скорость ответа.<sup>[3]</sup>",
    ending: "Выбор всё чаще зависит не от лидера бенчмарка, а от конкретной задачи: код, поиск по документам, работа в агентном контуре или локальный запуск."
  }
};

const form = document.querySelector('#askForm');
const input = document.querySelector('#question');
const conversation = document.querySelector('#conversation');
const hero = document.querySelector('#hero');
const sourcesList = document.querySelector('#sourcesList');
const sourcesEmpty = document.querySelector('#sourcesEmpty');
const sourceCount = document.querySelector('#sourceCount');

function renderSources(items = library) {
  sourcesList.innerHTML = '';
  sourcesEmpty.hidden = items.length > 0;
  sourceCount.textContent = String(items.length).padStart(2, '0');
  const template = document.querySelector('#sourceTemplate');
  items.forEach((item, index) => {
    const node = template.content.cloneNode(true);
    const card = node.querySelector('.source-card');
    card.href = item.href;
    node.querySelector('.source-number').textContent = `#${String(index + 1).padStart(2, '0')}`;
    node.querySelector('.source-date').textContent = item.date;
    node.querySelector('h3').textContent = item.title;
    node.querySelector('p').textContent = item.excerpt;
    item.tags.forEach(tag => {
      const tagNode = document.createElement('span');
      tagNode.className = 'tag';
      tagNode.textContent = tag;
      node.querySelector('.tags').append(tagNode);
    });
    sourcesList.append(node);
  });
}

function responseFor(question) {
  const value = question.toLowerCase();
  if (/агент|код|разработ/.test(value)) return responses.agents;
  if (/модел|openai|месяц/.test(value)) return responses.models;
  return responses.default;
}

function addQuery(question) {
  hero?.remove();
  const questionNode = document.createElement('div');
  questionNode.className = 'message question-message';
  questionNode.textContent = question;
  conversation.append(questionNode);

  const loading = document.createElement('div');
  loading.className = 'message typing';
  loading.innerHTML = '<i></i><i></i><i></i>';
  conversation.append(loading);
  input.value = '';
  input.style.height = 'auto';
  window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });

  window.setTimeout(() => {
    const answer = responseFor(question);
    const answerNode = document.createElement('article');
    answerNode.className = 'message';
    answerNode.innerHTML = `<div class="answer-header">ОТВЕТ ИЗ АРХИВА</div><div class="answer"><p>${answer.intro}</p><p>${answer.body}</p><p>${answer.ending}</p></div>`;
    loading.replaceWith(answerNode);
    renderSources();
    answerNode.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }, 700);
}

form.addEventListener('submit', event => {
  event.preventDefault();
  const question = input.value.trim();
  if (question) addQuery(question);
});

input.addEventListener('keydown', event => {
  if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) form.requestSubmit();
});

input.addEventListener('input', () => {
  input.style.height = 'auto';
  input.style.height = `${Math.min(input.scrollHeight, 120)}px`;
});

document.querySelectorAll('[data-prompt]').forEach(button => {
  button.addEventListener('click', () => addQuery(button.dataset.prompt));
});

document.querySelector('#newChat').addEventListener('click', () => {
  conversation.innerHTML = '';
  if (hero) conversation.append(hero);
  input.focus();
  renderSources([]);
});

renderSources();
