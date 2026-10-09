'''
OWASP Benchmark for Python v0.1

This file is part of the Open Web Application Security Project (OWASP) Benchmark Project.
For details, please see https://owasp.org/www-project-benchmark.

The OWASP Benchmark is free software: you can redistribute it and/or modify it under the terms
of the GNU General Public License as published by the Free Software Foundation, version 3.

The OWASP Benchmark is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
PURPOSE. See the GNU General Public License for more details.

  Author: Theo Cartsonis
  Created: 2025
'''

from flask import redirect, url_for, request, make_response, render_template
from helpers.utils import escape_for_html

def init(app):

	@app.route('/benchmark/cmdi-00/BenchmarkTest00167', methods=['GET'])
	def BenchmarkTest00167_get():
		return BenchmarkTest00167_post()

	@app.route('/benchmark/cmdi-00/BenchmarkTest00167', methods=['POST'])
	def BenchmarkTest00167_post():
		RESPONSE = ""

		param = request.form.get("BenchmarkTest00167")
		if not param:
			param = ""

		TestParam = "This should never happen"
		if 'should' not in TestParam:
			bar = "Ifnot case passed"
		else:
			bar = param

		import platform
		import re
		import subprocess
		import helpers.utils

		if platform.system() == "Windows":
			safe_bar = re.sub(r'[&|<>^%"\r\n]', '', bar)
			args = ["cmd.exe", "/c", "echo", safe_bar]
		else:
			args = ["sh", "-c", 'echo "$1"', "sh", bar]

		try:
			proc = subprocess.run(args, shell=False, capture_output=True, encoding="utf-8")

			RESPONSE += (
				helpers.utils.commandOutput(proc)
			)
		except IOError:
			RESPONSE += (
				"Problem executing cmdi - subprocess.run(list) Test Case"
			)

		return RESPONSE
