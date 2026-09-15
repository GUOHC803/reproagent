# mini-mlp

A minimal numpy MLP trained on a synthetic two-moons dataset. No GPU, runs in seconds.

```
python train.py --epochs 200 --lr 0.5 --hidden 16 --out metrics.json
```

Defaults live in `config.yaml`. The model is in `mlp/model.py`, the data generator in `mlp/data.py`.
`train.py` prints the loss every 10% of training and finally a JSON line with `final_loss`,
`best_loss`, `test_accuracy` (20% held-out split) and the hyper-parameters used.
