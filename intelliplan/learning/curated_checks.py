"""Fixed original beta activities for the two supported link-only curricula.

These are not learner mastery estimates or a general assessment generator.
Administrator review remains required. Verification data is for tests only;
no submitted or generated code is executed by the course worker.
"""
from copy import deepcopy


BANK = {
    ('python', 'variables and strings'): dict(
        id='python-strings-v1',
        explanation='A variable names a value. A string is text; joining two strings produces new text. Read the source, then predict a result before running an example.',
        worked_example='```python\nname = "Ada"\nprint(name + "!")\n```\nThe output is `Ada!`: the value of name is joined with the exclamation mark.',
        practice='Set name to a different name. Predict the output of joining it with " is learning". Explain why quoting "name" would change the result.',
        common_mistakes='A quoted variable name is literal text. Concatenation joins strings; it does not insert a space unless a string contains one.',
        review='Tomorrow, explain the difference between name and "name" without opening your notes.',
        quiz=dict(question='If name is "Lin", what does print(name + "!") output?',
            options=['name!', 'Lin!', 'Lin + !', 'Error'], answer=1,
            explanation='name supplies the string Lin. Joining it with ! gives Lin!, without an added space.'),
        verification=dict(kind='python_stdout', code='name = "Lin"\nprint(name + "!")')),
    ('python', 'conditions'): dict(
        id='python-conditions-v1',
        explanation='A condition selects a branch based on a true or false comparison. Indentation identifies the statements in that branch.',
        worked_example='```python\nn = 4\nif n > 3:\n    print("large")\nelse:\n    print("small")\n```\n4 > 3 is true, so only large is printed.',
        practice='Change n to 3. Predict the result. Then change > to >= and explain why the boundary case changes.',
        common_mistakes='Greater than excludes equality. The else branch runs when the condition is false, not after every if branch.',
        review='Recall one value that takes each branch and one boundary value.',
        quiz=dict(question='n is 2. An if branch prints "large" when n > 3; otherwise it prints "small". Which output occurs?',
            options=['large', 'small', 'large then small', 'No output'], answer=1,
            explanation='2 > 3 is false, so the else branch prints small.'),
        verification=dict(kind='python_stdout', code='n = 2\nif n > 3:\n    print("large")\nelse:\n    print("small")')),
    ('python', 'loops'): dict(
        id='python-loops-v1',
        explanation='A loop repeats an action for each value in a sequence. Trace the accumulator after each iteration instead of guessing the final result.',
        worked_example='```python\ntotal = 0\nfor n in range(1, 4):\n    total += n\nprint(total)\n```\nThe accumulator becomes 1, then 3, then 6. The endpoint 4 is excluded.',
        practice='Use range(2, 6). Write each accumulator value on paper, then run your own example and compare.',
        common_mistakes='range excludes its stop value. Resetting the accumulator inside the loop loses earlier additions.',
        review='Explain where to initialize an accumulator and why the stop value is excluded.',
        quiz=dict(question='total starts at 0. A loop adds each value in range(2, 5). What is total afterward?',
            options=['5', '7', '9', '14'], answer=2,
            explanation='The values are 2, 3 and 4, so total is 2 + 3 + 4 = 9.'),
        verification=dict(kind='python_stdout', code='total = 0\nfor n in range(2, 5):\n    total += n\nprint(total)')),
    ('python', 'functions'): dict(
        id='python-functions-v1',
        explanation='A function describes a reusable operation. A parameter receives an input, and return supplies the result to the caller.',
        worked_example='```python\ndef square(n):\n    return n * n\nprint(square(3))\n```\nThe call substitutes 3 for n and returns 9.',
        practice='Define a function that adds 2 to its input. Predict its result for a negative input and test your prediction.',
        common_mistakes='Defining a function does not call it. Printing inside a function is different from returning a value.',
        review='Describe the parameter, argument and returned result in a small example.',
        quiz=dict(question='square(n) returns n * n. What does print(square(4)) output?',
            options=['8', '16', '44', '4'], answer=1,
            explanation='The argument is 4, so n * n is 4 * 4 = 16.'),
        verification=dict(kind='python_stdout', code='def square(n):\n    return n * n\nprint(square(4))')),
    ('python', 'lists and dictionaries'): dict(
        id='python-collections-v1',
        explanation='A list stores an ordered sequence with zero-based positions. A dictionary maps keys to values. Choose the access operation that matches the structure.',
        worked_example='```python\nnames = ["Ada", "Lin"]\nscores = {"Ada": 3, "Lin": 5}\nprint(names[0])\nprint(scores["Lin"])\n```\nThe outputs are Ada and 5: position 0 selects the first list item; the key Lin selects its mapped score.',
        practice='Create a three-item list and a dictionary describing those items. Retrieve the last list item and one dictionary value, then explain their different selectors.',
        common_mistakes='The first list index is 0. A dictionary key is not necessarily a list-style numeric position.',
        review='Without notes, give one list lookup and one dictionary lookup and explain what each selector means.',
        quiz=dict(question='letters = ["A", "B", "C"]. What does print(letters[1]) output?',
            options=['A', 'B', 'C', 'Error'], answer=1,
            explanation='Index 0 selects A; index 1 selects the second item, B.'),
        verification=dict(kind='python_stdout', code='letters = ["A", "B", "C"]\nprint(letters[1])')),
    ('python', 'testing'): dict(
        id='python-testing-v1',
        explanation='A test states an expected result for a specific input. An assertion passes when its condition is true and raises AssertionError when it is false.',
        worked_example='```python\ndef double(n):\n    return 2 * n\nassert double(3) == 6\n```\nThis checks one input, not every possible input. Add boundary cases before trusting the function.',
        practice='Add tests for double(0) and double(-2). Explain which mistakes those examples could reveal and why passing tests do not prove every case.',
        common_mistakes='A test must compare against the intended result. Repeating the implementation without an independent expected value can hide the same mistake.',
        review='Recall a normal case, a boundary case and a negative-input case for a small function.',
        quiz=dict(question='double(n) returns 2 * n. Which assertion correctly checks its result for input 5?',
            options=['assert double(5) == 5', 'assert double(5) == 10', 'assert double(5) == 25', 'assert double(5) == 2'], answer=1,
            explanation='The intended result for input 5 is 10. The assertion compares the function result with that independently calculated value.'),
        verification=dict(kind='python_assertions', setup='def double(n):\n    return 2 * n')),
    ('algebra', 'variables'): dict(
        id='algebra-variables-v1',
        explanation='A variable represents a number. To evaluate an expression, substitute the stated value and then follow the order of operations.',
        worked_example='For x = 2, the expression 3x + 2 becomes 3 × 2 + 2 = 8. Multiplication occurs before addition.',
        practice='Evaluate 4x - 1 when x = 3 and when x = -2. Explain each substitution step.',
        common_mistakes='3x means 3 multiplied by x, not the digits written beside each other. Keep parentheses around a negative substituted value.',
        review='Explain the difference between a variable, an expression and an equation.',
        quiz=dict(question='When x = 4, what is the value of 3x + 2?',
            options=['6', '12', '14', '18'], answer=2,
            explanation='Substitute 4: 3 × 4 + 2 = 12 + 2 = 14.'),
        verification=dict(kind='linear_value', slope=3, intercept=2, x=4)),
    ('algebra', 'addition equations'): dict(
        id='algebra-addition-v1',
        explanation='An equation states that two expressions are equal. Apply the same addition or subtraction to both sides to preserve equality.',
        worked_example='To solve x + 4 = 9, subtract 4 from both sides: x = 5. Substitute back: 5 + 4 = 9.',
        practice='Solve x - 6 = 11. Name the operation used on both sides, then check the result by substitution.',
        common_mistakes='Changing only one side does not preserve equality. A solution should satisfy the original equation, not just the transformed one.',
        review='Recall how inverse operations and substitution support solving an equation.',
        quiz=dict(question='What value of x satisfies x + 7 = 15?',
            options=['7', '8', '15', '22'], answer=1,
            explanation='Subtract 7 from both sides to obtain x = 8. The check is 8 + 7 = 15.'),
        verification=dict(kind='linear_solution', a=1, b=7, rhs=15)),
    ('algebra', 'multiplication equations'): dict(
        id='algebra-multiplication-v1',
        explanation='To isolate a variable multiplied by a nonzero coefficient, divide both sides by that coefficient. Check the original equation afterward.',
        worked_example='For 3x = 12, divide both sides by 3 to get x = 4. Substitution gives 3 × 4 = 12.',
        practice='Solve 5x = 35 and x / 4 = 3. Explain why the appropriate inverse operations differ.',
        common_mistakes='A coefficient multiplies the variable; subtracting the coefficient does not undo that multiplication. Division by zero is undefined.',
        review='Give one multiplication equation and explain its inverse operation.',
        quiz=dict(question='What value of x satisfies 3x = 18?',
            options=['3', '6', '15', '54'], answer=1,
            explanation='Divide both sides by 3: x = 6. Substitution gives 3 × 6 = 18.'),
        verification=dict(kind='linear_solution', a=3, b=0, rhs=18)),
    ('algebra', 'coordinates'): dict(
        id='algebra-coordinates-v1',
        explanation='An ordered pair (x, y) gives a horizontal and vertical position. The signs of x and y identify a quadrant when neither coordinate is zero.',
        worked_example='The point (-2, 3) is left of the y-axis and above the x-axis. It lies in Quadrant II.',
        practice='Plot (3, 2), (-3, -2) and (0, 4). Explain why the point on an axis is not inside a quadrant.',
        common_mistakes='The x-coordinate comes first. Points on an axis are not assigned to a quadrant.',
        review='Recall the sign pair in each quadrant, starting with positive x and positive y.',
        quiz=dict(question='Which quadrant contains the point (2, -3)?',
            options=['Quadrant I', 'Quadrant II', 'Quadrant III', 'Quadrant IV'], answer=3,
            explanation='Positive x places the point right of the y-axis; negative y places it below the x-axis. That is Quadrant IV.'),
        verification=dict(kind='quadrant', x=2, y=-3)),
    ('algebra', 'slope'): dict(
        id='algebra-slope-v1',
        explanation='Slope measures change in y divided by the corresponding change in x. Use the same point order in numerator and denominator.',
        worked_example='Between (0, 1) and (2, 5), slope = (5 - 1) / (2 - 0) = 4 / 2 = 2.',
        practice='Find the slope through (1, 4) and (3, 2). Explain the negative sign and verify that reversing both point orders gives the same result.',
        common_mistakes='Do not reverse the point order in only one difference. A vertical line has zero change in x and no defined finite slope.',
        review='Explain slope using both a graph description and a ratio of differences.',
        quiz=dict(question='What is the slope of the line through (1, 2) and (3, 8)?',
            options=['1', '2', '3', '6'], answer=2,
            explanation='The change in y is 8 - 2 = 6; the change in x is 3 - 1 = 2. Their ratio is 3.'),
        verification=dict(kind='slope', points=[[1, 2], [3, 8]])),
    ('algebra', 'slope-intercept'): dict(
        id='algebra-intercept-v1',
        explanation='In y = mx + b, m is the slope and b is the y-value when x = 0. Thus b identifies the y-intercept.',
        worked_example='For y = 3x + 2, setting x = 0 gives y = 2. The line crosses the y-axis at (0, 2), and its slope is 3.',
        practice='Identify the slope and y-intercept of y = -2x + 5. Check your intercept by setting x to 0.',
        common_mistakes='The slope and intercept have different roles. The y-intercept lies where x is zero, not where y is zero.',
        review='Sketch a line from its slope and y-intercept, then explain each parameter.',
        quiz=dict(question='What is the y-intercept value of y = 2x + 3?',
            options=['0', '2', '3', '5'], answer=2,
            explanation='At the y-axis, x = 0, so y = 2 × 0 + 3 = 3. The intercept point is (0, 3).'),
        verification=dict(kind='linear_value', slope=2, intercept=3, x=0)),
}


def content_for(subject, objective):
    """Exact curriculum match only; edited objectives may have no fixed check."""
    if not isinstance(subject, str) or not isinstance(objective, str):
        return None
    item = BANK.get((subject, ' '.join(objective.casefold().split())))
    if item is None:
        return None
    fields = ('explanation', 'worked_example', 'practice', 'common_mistakes', 'review', 'quiz')
    return {**{key: deepcopy(item[key]) for key in fields},
            'assessment_origin': 'fixed_original_v1', 'assessment_bank_id': item['id'],
            'assessment_language': 'en'}
