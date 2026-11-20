# DeckImageMTG

App desktop gratuito e de código aberto, para **Linux e Windows**. Você cola o **main deck** e o **sideboard**
de uma lista de Magic: The Gathering, ele busca as cartas no [Scryfall](https://scryfall.com/docs/api) e gera
uma imagem única, **`final.jpg`**, na sua pasta **Downloads**.

O padrão é o formato **Premodern**, com as cartas na **impressão original** (a mais antiga).

> **Projeto de fã não oficial.** Magic: The Gathering, seus logos e as imagens das cartas são © Wizards of the Coast.
> Este projeto não é aprovado nem endossado pela Wizards of the Coast. Dados e imagens via Scryfall.
> Uso livre e gratuito, conforme a [Fan Content Policy](https://company.wizards.com/en/legal/fancontentpolicy).

---

## Como executar

### Opção 1: baixar o executável (sem instalar nada)

1. Abra a página de [**Releases**](../../releases/latest).
2. Baixe o arquivo do seu sistema:
   - **Windows:** `DeckImageMTG.exe`
   - **Linux:** `DeckImageMTG`
3. Rode:
   - **Windows:** dê dois cliques no `.exe`. O executável não é assinado, então o SmartScreen pode avisar:
     clique em *Mais informações* e depois em *Executar assim mesmo*.
   - **Linux:** no terminal, dentro da pasta onde o arquivo foi salvo:
     ```bash
     chmod +x DeckImageMTG
     ./DeckImageMTG
     ```

### Opção 2: rodar pelo código-fonte (Python)

Pré-requisito: **Python 3.10 ou mais novo**, com Tkinter.
- **Windows:** baixe em [python.org](https://www.python.org/downloads/). No instalador, marque *Add python.exe to PATH*
  e deixe marcada a opção *tcl/tk and IDLE*.
- **Linux:** o Python normalmente já vem instalado. Se faltar o Tkinter, instale `python3-tk` (Debian/Ubuntu),
  `python3-tkinter` (Fedora) ou `tk` (Arch).

**Linux / macOS**
```bash
git clone https://github.com/RSSPEREZ/mtg-deck-image.git
cd mtg-deck-image
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python run.py
```

**Windows (PowerShell ou Prompt de Comando)**
```bat
git clone https://github.com/RSSPEREZ/mtg-deck-image.git
cd mtg-deck-image
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python run.py
```

Sem `git`, use **Code → Download ZIP** aqui no GitHub e extraia a pasta.

---

## Como usar

### Pela janela

1. Cole o **main deck** no campo da esquerda e o **sideboard** no da direita.
2. Se quiser, preencha o **título** (aparece no topo da imagem), o número de **colunas** e o **formato**.
3. Clique em **Gerar final.jpg**.
4. No fim, o app mostra onde salvou a imagem, quais cartas ficaram de fora e se quer abrir a imagem.

A imagem vai para `~/Downloads/final.jpg` no Linux e para `C:\Users\<você>\Downloads\final.jpg` no Windows.
Cada geração **substitui** o `final.jpg` anterior.

### Formato da lista

Funciona com o texto exportado por MTGO, Arena, Moxfield, MTGGoldfish e similares:

```
4 Goblin Lackey
4x Goblin Matron
2 Siege-Gang Commander (SCG) 107
16 Mountain

Sideboard
4 Pyroblast
SB: 3 Tormod's Crypt
```

- `4 Nome` ou `4x Nome`. Sem número, conta como 1 cópia.
- `(SET) número` fixa uma edição específica.
- Uma linha `Sideboard` manda o resto para o side, então dá para colar a lista inteira no campo da esquerda.
  O prefixo `SB:` também funciona.
- Linhas que começam com `//` ou `#` são ignoradas.
- Nomes com pequenos erros de digitação são corrigidos automaticamente, e a correção aparece no log.

### Pela linha de comando

```bash
python run.py --main deck.txt --side side.txt --title "Goblins"
```

| Opção | Padrão | Descrição |
|---|---|---|
| `--main` | — | arquivo `.txt` do main deck (pode conter a linha `Sideboard`) |
| `--side` | — | arquivo `.txt` do sideboard |
| `--title` | vazio | título no topo da imagem |
| `--format` | `premodern` | filtro de legalidade (`legacy`, `vintage`, `pauper`, `oldschool`… ou `none` para desligar) |
| `--cols` | `8` | cartas por linha |
| `--out` | `Downloads/final.jpg` | caminho da imagem gerada |

O comando retorna código `1` quando alguma carta não foi encontrada ou ficou fora do formato.

---

## Regras de geração

- **Formato (padrão Premodern):** cartas banidas ou que não são legais no formato **não entram na imagem**
  e são listadas no aviso final. A opção *(sem filtro)* ou `--format none` desliga o filtro.
- **Edição:** carta sem edição na lista usa a **impressão mais antiga** em papel, legal no formato e sem promo.
  Para escolher outra edição, escreva `(SET) número` na linha.
- **Layout:** cada carta aparece uma vez, com a quantidade embaixo (`4x`). Main deck e sideboard ficam em
  seções separadas, cada uma com seu total.
- As imagens das cartas **não são alteradas**: nada é desenhado por cima, e o texto de copyright continua visível.

## Respeito à API do Scryfall

O Scryfall é mantido de graça para a comunidade, e o app segue as
[regras de uso da API](https://scryfall.com/docs/api):

- Todas as requisições levam `User-Agent: DeckImageMTG/<versão>` e `Accept`.
- No mínimo **100 ms entre quaisquer requisições** (API e imagens), ou seja, menos de 10 por segundo.
  Se vier um HTTP 429, o app espera e tenta de novo.
- As cartas são buscadas **em lote** (`/cards/collection`, 75 por chamada, mais um `/cards/search` com `prefer:oldest`),
  em vez de uma chamada por carta. Um deck de 75 cartas costuma usar cerca de 3 chamadas à API mais as imagens.
- As imagens ficam em **cache local**, então gerar a mesma lista de novo não baixa nada:
  - Linux: `~/.cache/deckimage-mtg/`
  - Windows: `%LOCALAPPDATA%\deckimage-mtg\`

  Para liberar espaço, basta apagar essa pasta.

---

## Gerar os executáveis

- **Linux:** `./build_linux.sh` gera `dist/DeckImageMTG`.
- **Windows:** `build_windows.bat` gera `dist\DeckImageMTG.exe`.
- **GitHub Actions:** ao enviar uma tag `v*` (ex.: `git tag v1.1 && git push origin v1.1`), o workflow
  `.github/workflows/build.yml` gera os dois executáveis e publica uma Release com eles.

O PyInstaller não faz build cruzado: o `.exe` precisa ser gerado no Windows ou pelo Actions.

## Estrutura

```
run.py                 ponto de entrada (sem argumentos abre a janela; com argumentos, modo linha de comando)
deckimage/decklist.py  leitura da lista
deckimage/scryfall.py  cliente da API (rate limit, headers, lote, cache)
deckimage/build.py     resolução das cartas, filtro de formato e montagem da imagem
deckimage/app.py       janela (Tkinter) e linha de comando
```

## Contribuindo

Issues e pull requests são bem-vindos: bugs, novos formatos de lista, opções de layout, traduções.
Mantenha o respeito às regras do Scryfall (rate limit, headers, nada de chamadas por carta em loop)
e à Fan Content Policy (nada de logos oficiais nem de cobrar pelo acesso às imagens).

## Licença

Código sob a [licença MIT](LICENSE). A licença cobre só o código-fonte: as imagens e os dados das cartas
pertencem à Wizards of the Coast e são fornecidos pelo Scryfall.
