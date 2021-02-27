from flask import Flask, render_template, request, redirect, url_for, flash, make_response, session
from werkzeug.utils import secure_filename
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from collections import defaultdict
import random
import bs4
import requests
import csv

NUM_SECONDS_IN_MONTH = 2628000

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'mysql+mysqldb://root:themeforcities@localhost:3306/sys'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy()


# Dependency Functions
def update_calories(food, kcal, time_of_day):
    # Takes a single meal, processes it
    if kcal == '':
        # When the calories were omitted -- scrape it if possible
        food = food.replace(' ', '+')
        food = food.replace('&', 'and')
        calorie_link = f'https://www.nutracheck.co.uk/CaloriesIn/Product/Search?desc={food}'
        calories_site = requests.get(calorie_link)

        try:
            calories_site.raise_for_status()
            calories_site = calories_site.text

            soup = bs4.BeautifulSoup(calories_site, 'html.parser')
            kcal = soup.select()
        except:
            kcal = '0'

    # Also need to append the current date (eg 16/02/21) to the time of day
    current_date = datetime.utcnow().strftime('%d/%m/%y')
    time_of_day = f'{time_of_day}, {current_date}'

    # Now we will have a KCAL value no matter what
    # So update the DB.
    new_meal_to_add = Calories(food=food, kcal=kcal, time_of_day=time_of_day)
    db.session.add(new_meal_to_add)
    db.session.commit()


def read_calories_file(filename):
    with open(filename) as calories_csv:
        # Creates list of meals (lists)
        meals = [meal.strip().split(',') for meal in calories_csv.readlines()]

    # Returns the header line of CSV, and the data list of lists
    return meals[0], meals[1:]


def request_bmi(weight, height, inches=0):
    # first need to convert weight in stone, to lbs
    weight *= 14

    # height takes a height converted to feet, and any extra inches
    height = (height * 12) + inches
    return round((weight / (height ** 2)) * 703, 1)


def generate_secret_key(length=16):
    random_characters = []
    for i in range(length):
        random_char = random.randint(33, 126)   # any character really
        random_characters.append(chr(random_char))

    return "".join(random_characters)


SECRET_KEY = generate_secret_key()
app.secret_key = SECRET_KEY
print(f'Generated secret key: {SECRET_KEY}')


# Database models
class LoggedWeight(db.Model):
    weight = db.Column(db.String(255), primary_key=True, nullable=False)
    date = db.Column(db.Date(), primary_key=True, nullable=False)

    def __repr__(self):
        return '<LoggedWeight %r>' % self.weight


class Calories(db.Model):
    calories_id = db.Column(db.Integer(), nullable=False, primary_key=True, autoincrement=True, unique=True)
    food = db.Column(db.String(255), nullable=False)
    kcal = db.Column(db.String(20), nullable=False)
    date = db.Column(db.String(40), nullable=False)

    def __repr__(self):
        return '<Calories %r>' % self.calories_id


db.init_app(app)


# Routes
@app.route('/')
def home():
    logged_weights = LoggedWeight.query.order_by(LoggedWeight.date.desc()).all()

    for logged_weight in logged_weights:
        # Convert DB stored poor looking date to a more user friendly version
        original_time = datetime.strptime(str(logged_weight.date), '%Y-%m-%d')
        new_time_object = original_time.strftime('%d %B %Y')
        logged_weight.date = new_time_object

    # temp solution -- use JS for this whole thing instead!
    # this just copies the bmi from session and passes into template, then removes from session
    # to stop it appearing after refresh
    bmi = session.get('calculated_bmi')
    session['calculated_bmi'] = None

    return render_template('home.html',
                           title='Home',
                           logged_weights=logged_weights,
                           cookies=request.cookies,
                           todays_date=datetime.utcnow().strftime('%Y-%m-%d'),
                           bmi=bmi
                           )


@app.route('/upload/', methods=['POST'])
def upload():
    if request.method == 'POST':
        weight = request.form['weight']
        date = request.form['date']

        new_weight_to_add = LoggedWeight(weight=weight, date=date)

        try:
            db.session.add(new_weight_to_add)
            db.session.commit()
        except:
            error_message = f'Tried to add weight: <{weight}> and date: <{date}>' \
                            f' to the database, but failed in the process.'
            app.logger.info(error_message)
            flash(error_message)
            return redirect(url_for('home'))
        else:
            return redirect(url_for('home'))


@app.route('/delete/<date>/<weight>/', methods=['POST'])
def delete_record(date, weight):
    if request.method == 'POST':
        # change date to the correct format
        original_time = datetime.strptime(str(date), '%d %B %Y')
        new_date_object = original_time.strftime('%Y-%m-%d')

        record_to_delete = LoggedWeight.query\
            .filter(LoggedWeight.weight == weight)\
            .filter(LoggedWeight.date == new_date_object).first()

        db.session.delete(record_to_delete)
        db.session.commit()

    return redirect(url_for('home'))


@app.route('/set_target_weight/', methods=['POST'])
def set_target_weight():
    # sets a cookie for the users target weight
    if request.method == 'POST':
        target_weight = request.form['target_weight']

        resp = make_response(redirect(url_for('home')))
        resp.set_cookie('weight_target', target_weight, max_age=NUM_SECONDS_IN_MONTH)

        return resp


@app.route('/remove_target_weight/', methods=['POST'])
def remove_target_weight():
    resp = make_response(redirect(url_for('home')))
    resp.delete_cookie('weight_target')

    return resp


@app.route('/calculate_bmi/', methods=['POST'])
def calculate_bmi():
    weight = request.form['weight']
    height = request.form['height_feet']
    inches = request.form['height_inches']

    if not inches:
        # default to 0 if the input was empty
        inches = 0

    bmi = request_bmi(float(weight), int(height), int(inches))
    session['calculated_bmi'] = bmi

    return redirect(url_for('home'))


@app.route('/calories/')
def calorie_counter():
    meals = Calories.query.order_by(Calories.date.desc()).all()

    # We now have a list of tuples. We want a dict with dates as keys and the values being a
    # list of tuples for meals on that date.
    # So we can display these in HTML on separate cards easily.
    meals_by_date = defaultdict(list)
    for meal in meals:
        date_to_display = datetime.strptime(meal.date, '%Y-%m-%d').strftime('%b %d %Y')
        meal.kcal = int(meal.kcal)  # convert the kcal from string, as stored in DB, to an int
        meals_by_date[date_to_display] += [meal]

    return render_template('calories.html',
                           title='Calories',
                           meals=meals_by_date,
                           todays_date=datetime.now().strftime('%Y-%m-%d')
                           )


@app.route('/log_calories/', methods=['POST'])
def log_calories():
    food = request.form['food']
    kcal = request.form['kcal']
    date = request.form['date']

    new_calorie_object = Calories(food=food, kcal=kcal, date=date)
    db.session.add(new_calorie_object)
    db.session.commit()

    return redirect(url_for('calorie_counter'))


@app.route('/log_calories_from_csv/', methods=['POST'])
def log_calories_from_csv():
    calories_csv = request.files['calories_csv']
    date = request.form['date']

    calories_csv = secure_filename(calories_csv.filename)

    with open(calories_csv) as calories_csv_file:
        lines = csv.reader(calories_csv_file)
        meals_to_add = []

        for line in lines:
            food, kcal = line
            meals_to_add.append(Calories(food=food, kcal=kcal, date=date))

    db.session.add_all(meals_to_add)
    db.session.commit()

    return redirect(url_for('calorie_counter'))


@app.route('/delete/<calories_id>/', methods=['POST'])
def delete_meal(calories_id):
    meal_to_remove = Calories.query.filter(Calories.calories_id == calories_id).first()
    db.session.delete(meal_to_remove)
    db.session.commit()

    return redirect(url_for('calorie_counter'))


@app.route('/delete_all_calorie_logs/<date>/', methods=['POST'])
def delete_all_calorie_logs(date):
    date = datetime.strptime(date, '%b %d %Y').strftime('%Y-%m-%d')
    Calories.query.filter_by(date=date).delete()

    db.session.commit()

    return redirect(url_for('calorie_counter'))


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0')
