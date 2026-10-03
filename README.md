# personal-agentic-accountant

Expert personal Chief Financial Officer (CFO), financial strategist, and wealth advisor tool:
a multi-agent system.
Tell it your goals, hand it your financial documents, and a team of specialist agents
builds you one prioritised plan, with every figure computed by tested calculators, not guessed.

Portugal is fully supported (2026 IRS, IMT, deductions, IRS Jovem, IFICI, PPR, Certificados…).
Other countries work too: budgeting, debt and investing are country-independent, and tax
figures are clearly marked as indicative until a country pack is added.

> Planning information, not regulated financial advice. Confirm important tax or legal decisions
> with a certified accountant (contabilista certificado) or lawyer.

## Quick start

```bash
pip install -r requirements.txt        # pyyaml; plus litellm, pypdf, openpyxl (recommended)
export ANTHROPIC_API_KEY=...           # or OPENAI_API_KEY, or use a local model (see Privacy)
python start.py
```

No key? It still runs in offline demo mode: documents are read, the plan and every calculator
work, but the agents' written answers are placeholders. Try it with the fictional sample:
answer the questions and give `examples/sample-household` when asked for documents.

## What happens

```
1. Where are you tax resident? [Portugal]:
2. What do you want to achieve?            > Pay off my debts faster
                                           > Pay less IRS and start investing
A few quick facts (Enter to skip any):     age, household, dependants, work, IRS Jovem/IFICI, risk, language
3. Your documents. Fastest: give me ONE folder with everything.
   > ~/Documents/finances
   Read 4 document(s):
    • Bank statements: extrato_conta_jul-set_2026.csv
    • Credit responsibility map (Banco de Portugal): mapa_responsabilidades_credito.txt
    • Last IRS return and tax assessment: nota_liquidacao_irs_2025.txt
    • Payslips: recibo_vencimento_2026-09.txt

For your goals these matter most. For each: a path, OR type the key figures, OR press Enter to skip.
• e-fatura deductible expenses summary (Despesas dedutíveis (e-fatura))
  Where: Portal das Finanças > e-fatura > Consumidor > Despesas dedutíveis em IRS
  Why:   Shows how much of each deduction (health, education, general, VAT) you're already getting.

• Savings and investment statements (Depósitos, Certificados de Aforro/Tesouro, PPR, corretora)
  > path, or What savings/investments you have and roughly how much in each? about 3000 EUR, nothing invested

How complete is the picture for your goals:
  ~ tax        missing: e-fatura deductible expenses summary
  ~ debt       missing: Loan contracts or statements (mortgage, personal, car), Credit card statements
  ✓ wealth     all key items provided

Proposed team: 5 agents. a bookkeeper first organises your documents; 3 specialists cover tax, debt,
wealth; the CFO combines everything into one plan.
Start?  [Enter] yes   [1] CFO only   [e] pick specialists   [q] quit:
```

- **It asks for what your goals need.** The document checklist comes from the country pack, and
  each item says where to get it and why it matters. Every document is optional, and you can type
  the key figures instead.
- **It decides how big the team should be.** A single focused question goes to the CFO alone; broader goals get
  the relevant specialists, a bookkeeper when there are documents, and the CFO last.
- **It suggests, it doesn't insist.** If your team choice leaves a goal uncovered, it says so once;
  the default keeps your choice. Agents are told to challenge a sub-optimal plan with numbers and
  then show the best way to do it your way.
- **The output** is `report.md`: where you stand, an answer per goal, an action plan
  (this week, this month, next 3 months, this year), the numbers to track, risks and when to see a professional.

## The team

| Agent | Does | Main tools |
|---|---|---|
| Bookkeeper | Turns documents into one financial snapshot; flags gaps and inconsistencies | budget_analysis, net_worth, categorize_transactions |
| Tax strategist | Estimates IRS, compares filing options, quantifies legal savings | estimate_income_tax, retirement_savings_benefit, capital_income_tax |
| Cash-flow controller | Budget, 12-month cash flow, sinking fund for yearly bills, emergency fund | cash_flow_projection, sinking_fund_plan, emergency_fund_target |
| Debt strategist | Ranks debts, payoff plan, early-repayment maths (fees, notice periods) | debt_payoff_plan, early_repayment_analysis |
| Property advisor | Affordability, purchase taxes (IMT, stamp duty), buy vs rent | property_purchase_costs, loan_payment |
| Wealth advisor | Order of operations, allocation, goal and retirement projections | investment_options, savings_projection, retirement_projection |
| Protection advisor | Insurance gaps and overlaps, estate basics | emergency_fund_target |
| CFO | Resolves trade-offs and writes the plan | goal_planner, cash_flow_projection |

All tools are plain Python in `plugins/`, covered by tests against published tables and hand calculations.

## Privacy

- Documents are read **on your machine**. Bank statements are parsed and summarised locally.
- Before anything goes to a model, **tax numbers (NIF, checksum-validated), social security numbers,
  IBANs, card numbers (Luhn-validated), emails and phone numbers are masked**. Names and addresses
  are not; remove them yourself if that matters (`--no-redact` turns masking off).
- What is sent goes to the model provider you choose. To keep everything local, use a local model:
  `python start.py --model ollama_chat/llama3.1` (needs [Ollama](https://ollama.com) and a model with tool support).
- Each engagement is saved in `workspaces/` (git-ignored). Delete it when you're done.

## Re-running and updating

```bash
python run.py workspaces/2026-10-03-pay-off-my-debts-faster
python run.py workspaces/2026-10-03-pay-off-my-debts-faster --model anthropic/claude-opus-5-5
```

Edit `input.json` (e.g. a new balance) or `config.yaml` (e.g. a prompt) and re-run.

## Countries

Everything country-specific lives in `jurisdictions/<code>/pack.yaml`: tax tables, deductions,
property taxes, bank rules, the investment-options catalogue, the document checklist and the
transaction categories. The code holds no country's numbers.

- `pt`: Portugal, tax year 2026, verified 2026-10-03, with sources listed in the pack.
- `generic`: used for any other country; tax tools answer "not available" rather than invent figures.

Each pack records when its figures were verified. **Re-check the Portugal pack after each State Budget.**
See [docs/adding-a-country.md](docs/adding-a-country.md).

## Layout

```
start.py                guided intake (main entry point)
run.py                  re-run a saved engagement
cfo/
  planner.py            goals -> domains -> documents needed -> team; payload and config
  team.py               the specialist roles and their shared rules
  documents.py          read PDF/Excel/CSV/text, classify against the checklist, redact identifiers
  statements.py         bank statement parsing, categorisation, recurring payments
  jurisdictions.py      country pack loading
plugins/
  finance_tools.py      loans, debt payoff, savings, goals, emergency fund, sinking fund, budget, net worth, retirement, cash flow
  tax_tools.py          pack-driven income tax, net salary, capital income, property purchase, PPR benefit, facts lookup
  statement_tools.py    transaction categorisation for agents
  general_tools.py      calculator, regex
core/                   multi-agent engine (from agentic-canvas): agents, orchestrator, model clients, runner
jurisdictions/          country packs
examples/sample-household/  fictional documents for trying it out
tests/                  python -m unittest discover -s tests -t .
```

## Development

```bash
python -m unittest discover -s tests -t .
```

The engine (`core/`, `plugins/base_tool.py`) comes from
[agentic-canvas](https://github.com/deprivedFool/agentic-canvas).
