.PHONY: demo test serve build eval doctor clean

demo:
	./run.sh demo

test:
	PYTHONPATH=src:tests python3 -m unittest discover -s tests -v

build:
	./run.sh build data/parsed --incremental

eval:
	./run.sh eval --index data/index --report data/eval_report.json

serve:
	./run.sh serve --open

doctor:
	./run.sh doctor

clean:
	rm -rf data/parsed data/index data/eval_report.json data/watch.log
