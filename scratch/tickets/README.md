# Tickets — paralelo

Ondas. Dentro de uma onda, as tickets **não compartilham arquivos**.

```
Onda A (4 em paralelo)
  T1 salvage          T2 canonicalize          T3 prompt          T4 recover (mocks)
Onda B (1)
  T5 analyze() liga T1+T2+T4
Onda C (2 em paralelo, depois de T5)
  T6 contrato e2e     T7 README
```

`T4` depende só das **assinaturas** em `SPEC-canonicalizer.md`, não do merge de T1/T2.

Não abrir PR por ticket se a suíte da main tiver de ficar verde: trabalhar na mesma branch; conflitos esperados só em `inventory.py` na T5.
